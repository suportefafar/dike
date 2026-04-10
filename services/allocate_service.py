"""
Service de alocação/sugestão de vaga para novas reservas.

Refatoração de allocate-reservations.py em classe reutilizável.
Usa OR-Tools CP-SAT para encontrar opções com mínimo de remanejamentos.

Otimizações aplicadas:
  - Pré-computação de overlaps e capacidades (fora do loop de salas).
  - Escopo reduzido do solver: apenas reservas relevantes por sala candidata.
  - Metadata de timestamp/status para mitigar race conditions no consumidor.
"""

from datetime import datetime, timezone

from ortools.sat.python import cp_model


class AllocateService:
    """Busca opções de alocação para uma nova reserva."""

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def time_to_seconds(t_str):
        """Converte 'HH:MM' para segundos desde meia-noite."""
        h, m = map(int, t_str.split(':'))
        return h * 3600 + m * 60

    @staticmethod
    def safe_int(value, default=0):
        """Conversão segura para inteiro."""
        try:
            if value is None or str(value).strip() == "":
                return default
            return int(float(value))
        except (ValueError, TypeError):
            return default

    @classmethod
    def overlaps(cls, res1, res2):
        """
        Verifica sobreposição temporal entre duas reservas
        considerando intervalo de datas, dias da semana e horários.
        """
        d1 = res1.get('data', res1)
        d2 = res2.get('data', res2)

        # 1. Intervalo de datas
        start1 = datetime.strptime(d1['date'], '%Y-%m-%d')
        end1 = datetime.strptime(
            d1.get('end_date') or d1['date'], '%Y-%m-%d'
        )
        start2 = datetime.strptime(d2['date'], '%Y-%m-%d')
        end2 = datetime.strptime(
            d2.get('end_date') or d2['date'], '%Y-%m-%d'
        )

        if start1 > end2 or start2 > end1:
            return False

        # 2. Dias da semana
        is_w1 = d1.get('is_weekly', True)
        is_w2 = d2.get('is_weekly', True)

        w1 = set(d1.get('weekdays', []))
        w2 = set(d2.get('weekdays', []))

        if not w1:
            w1 = {start1.weekday()}
        if not w2:
            w2 = {start2.weekday()}

        if not w1.intersection(w2):
            return False

        # 3. Horários
        s1 = cls.time_to_seconds(d1['start_time'])
        e1 = cls.time_to_seconds(d1['end_time'])
        s2 = cls.time_to_seconds(d2['start_time'])
        e2 = cls.time_to_seconds(d2['end_time'])

        return max(s1, s2) < min(e1, e2)

    # ------------------------------------------------------------------ #
    #  Método principal                                                   #
    # ------------------------------------------------------------------ #

    @classmethod
    def allocate(cls, new_reservation, places, existing_reservations,
                 limit_moves=3):
        """
        Busca opções de alocação para uma nova reserva.

        Args:
            new_reservation: dict flat com dados da nova reserva
                (title, date, start_time, end_time, capacity_needed, ...).
            places: lista de locais (cada um com 'id' e 'data').
            existing_reservations: lista de reservas existentes
                (cada uma com 'id' e 'data').
            limit_moves: máximo de remanejamentos permitidos (padrão: 3).

        Returns:
            dict com 'total_options', 'options', 'solved_at' e
            'solver_status' (por opção).
        """
        # Filtrar por tipos de sala permitidos
        allowed_types = [
            "classroom", "living_room", "computer_lab", "multimedia_room"
        ]
        places = [
            p for p in places
            if p.get('data', {}).get('object_sub_type', [None])[0]
            in allowed_types
        ]

        # Normalizar weekdays: JSON usa 1=Seg...7=Dom → Python 0=Seg...6=Dom
        for res in existing_reservations:
            if 'weekdays' in res.get('data', {}):
                try:
                    res['data']['weekdays'] = [
                        int(w) - 1 for w in res['data']['weekdays']
                    ]
                except TypeError as e:
                    raise TypeError(
                        f"Error in 'weekdays' param for reservation id={res.get('id')}. "
                        f"Value received was: {res['data']['weekdays']}. "
                        f"Original error: {e}"
                    ) from e

        # Normalizar nova reserva para formato com 'data'
        new_res = {
            "id": "new_reservation_request",
            "data": new_reservation,
        }

        # Mapa de capacidade por disciplina
        subject_cap = {}
        if subjects:
            if isinstance(subjects, dict):
                subjects = list(subjects.values())
                
            subject_cap = {
                s['id']: cls.safe_int(
                    s['data'].get('number_vacancies_offered')
                )
                for s in subjects
            }

        all_places = {p['id']: p for p in places}
        place_ids = list(all_places.keys())

        if not place_ids:
            return {"total_options": 0, "options": []}

        # ------------------------------------------------------------ #
        # Pré-computação (executada UMA vez, fora do loop de salas)     #
        # ------------------------------------------------------------ #
        req_to_solve = [new_res] + existing_reservations

        # Capacidade necessária por reserva
        req_capacities = []
        for req in req_to_solve:
            rd = req.get('data', req)
            c = cls.safe_int(rd.get('capacity_needed', 0))
            req_capacities.append(c)

        cap_needed_new = req_capacities[0]

        print(f"Capacidade necessária para a nova reserva: {cap_needed_new}")

        # Capacidade por sala (indexada pela posição em place_ids)
        place_capacities = [
            cls.safe_int(all_places[pid]['data'].get('capacity'))
            for pid in place_ids
        ]

        # Salas candidatas: capacidade >= necessidade da nova reserva
        candidate_pids = [
            pid for idx, pid in enumerate(place_ids)
            if place_capacities[idx] >= cap_needed_new
        ]

        print(f"Total de salas candidatas: {len(candidate_pids)}")

        if not candidate_pids:
            return {"total_options": 0, "options": []}

        # Matriz de conflitos O(n²) — computada uma única vez
        n = len(req_to_solve)
        conflict_pairs = []
        for i in range(n):
            for k in range(i + 1, n):
                if cls.overlaps(req_to_solve[i], req_to_solve[k]):
                    conflict_pairs.append((i, k))

        # Adjacência de conflitos para expansão rápida
        conflict_adj = {i: set() for i in range(n)}
        for i, k in conflict_pairs:
            conflict_adj[i].add(k)
            conflict_adj[k].add(i)

        # Sala original de cada reserva existente
        orig_place_map = {}
        for i in range(1, n):
            orig_ids = req_to_solve[i].get('data', {}).get('place', [])
            orig_place_map[i] = orig_ids[0] if orig_ids else None

        # ------------------------------------------------------------ #
        # Solver por sala candidata                                     #
        # ------------------------------------------------------------ #
        results = []

        for target_pid in candidate_pids:
            target_idx = place_ids.index(target_pid)

            # Determinar índices de reservas relevantes para este solver
            relevant = {0}  # nova reserva sempre presente

            # Quem conflita diretamente com a nova reserva
            relevant |= conflict_adj[0]

            # Quem já está alocado na sala-alvo
            for i in range(1, n):
                if orig_place_map.get(i) == target_pid:
                    relevant.add(i)

            # Expandir: reservas que conflitam com as relevantes
            # (necessário para que o solver consiga reacomodar cascatas)
            expanded = set(relevant)
            for idx in list(relevant):
                expanded |= conflict_adj[idx]
            relevant = expanded

            relevant_list = sorted(relevant)
            local_n = len(relevant_list)

            # Mapeamento local ↔ global
            local_to_global = {li: gi for li, gi in enumerate(relevant_list)}
            global_to_local = {gi: li for li, gi in enumerate(relevant_list)}

            model = cp_model.CpModel()

            x = {}
            for i in range(local_n):
                for j in range(len(place_ids)):
                    x[i, j] = model.NewBoolVar(f'x_{i}_{j}')

            # Cada reserva em exatamente 1 sala
            for i in range(local_n):
                model.Add(
                    sum(x[i, j] for j in range(len(place_ids))) == 1
                )

            # Sem sobreposição na mesma sala (apenas pares relevantes)
            for gi, gk in conflict_pairs:
                if gi in global_to_local and gk in global_to_local:
                    li = global_to_local[gi]
                    lk = global_to_local[gk]
                    for j in range(len(place_ids)):
                        model.Add(x[li, j] + x[lk, j] <= 1)

            # Restrição de capacidade
            for li in range(local_n):
                gi = local_to_global[li]
                c_needed = req_capacities[gi]
                if c_needed > 0:
                    for j in range(len(place_ids)):
                        if place_capacities[j] < c_needed:
                            model.Add(x[li, j] == 0)

            # Fixar nova reserva nesta sala candidata
            model.Add(x[global_to_local[0], target_idx] == 1)

            # Variáveis de remanejamento
            is_moved = []
            for li in range(local_n):
                gi = local_to_global[li]
                if gi == 0:
                    continue  # nova reserva, não é remanejamento

                orig_id = orig_place_map.get(gi)
                if orig_id and orig_id in place_ids:
                    orig_idx = place_ids.index(orig_id)
                    m = model.NewBoolVar(f'moved_{gi}_{target_pid}')
                    model.Add(m == 1).OnlyEnforceIf(
                        x[li, orig_idx].Not()
                    )
                    model.Add(m == 0).OnlyEnforceIf(x[li, orig_idx])
                    is_moved.append((li, gi, m))
                else:
                    # Sem sala original conhecida — não conta como move
                    pass

            move_vars = [m for _, _, m in is_moved]
            model.Minimize(sum(move_vars))
            model.Add(sum(move_vars) <= limit_moves)

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 2.0
            status = solver.Solve(model)

            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                moves_detail = []
                for li, gi, m_var in is_moved:
                    if solver.Value(m_var):
                        # Descobrir para qual sala foi movida
                        for j, pid in enumerate(place_ids):
                            if solver.Value(x[li, j]):
                                moves_detail.append({
                                    "reservation_id": req_to_solve[gi]['id'],
                                    "to_place": pid,
                                })
                                break

                results.append({
                    "place_id": target_pid,
                    "place_number": all_places[target_pid]['data'].get(
                        'number', ''
                    ),
                    "place_capacity": cls.safe_int(
                        all_places[target_pid]['data'].get('capacity')
                    ),
                    "moves_count": len(moves_detail),
                    "moves": moves_detail,
                    "solver_status": (
                        "OPTIMAL" if status == cp_model.OPTIMAL
                        else "FEASIBLE"
                    ),
                })

        # Ordenar por quantidade de remanejamentos
        results.sort(key=lambda r: r['moves_count'])

        return {
            "total_options": len(results),
            "options": results,
            "solved_at": datetime.now(timezone.utc).isoformat(),
        }
