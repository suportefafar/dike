"""
Service de alocação/sugestão de vaga para novas reservas.

Refatoração de allocate-reservations.py em classe reutilizável.
Usa OR-Tools CP-SAT para encontrar opções com mínimo de remanejamentos.

Otimizações aplicadas:
  - Reservas indexadas por sala.
  - Conflitos computados sob demanda e propagados para a rede de dependência (hop 1).
  - Escopo reduzido do solver: apenas variáveis para as reservas atreladas
    ao problema e todas as salas do campus.
  - Overlap check rápido e com datas pré-parseadas.
  - Correção principal: extraída capacidade apenas de 'capacity_needed'
  - Normalizeção universal dos dias da semana (weekdays) de 1-indexed para 0-indexed.
"""

from collections import defaultdict
from datetime import datetime, timezone

from ortools.sat.python import cp_model


class AllocateService:
    """Busca opções de alocação para uma nova reserva."""

    ALLOWED_ROOM_TYPES = frozenset(
        ["classroom", "living_room", "computer_lab", "multimedia_room"]
    )

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def time_to_seconds(t_str):
        h, m = map(int, t_str.split(':'))
        return h * 3600 + m * 60

    @staticmethod
    def safe_int(value, default=0):
        try:
            if value is None or str(value).strip() == "":
                return default
            return int(float(value))
        except (ValueError, TypeError):
            return default

    @classmethod
    def _preparse_reservation(cls, res):
        """Pré-processa dados de data/hora na reserva no momento do parse."""
        d = res.get('data', res)
        if not d.get('start_time') or not d.get('end_time') or not d.get('date'):
            return False

        try:
            res['_start_dt'] = datetime.strptime(d['date'], '%Y-%m-%d')
        except (ValueError, TypeError):
            return False

        end_date = d.get('end_date')
        if end_date and isinstance(end_date, str) and end_date.strip():
            try:
                res['_end_dt'] = datetime.strptime(end_date, '%Y-%m-%d')
            except (ValueError, TypeError):
                res['_end_dt'] = res['_start_dt']
        else:
            res['_end_dt'] = res['_start_dt']

        # Normalizar weekdays para int
        raw_w = d.get('weekdays')
        wd_set = set()
        if raw_w and isinstance(raw_w, list):
            for w in raw_w:
                try: wd_set.add(int(w))
                except (ValueError, TypeError): pass

        if not wd_set:
            res['_weekdays'] = {res['_start_dt'].weekday()}
        else:
            res['_weekdays'] = wd_set

        try:
            res['_s_sec'] = cls.time_to_seconds(d['start_time'])
            res['_e_sec'] = cls.time_to_seconds(d['end_time'])
        except (ValueError, TypeError):
            return False

        return True

    @staticmethod
    def _overlaps_fast(res1, res2):
        if res1['_start_dt'] > res2['_end_dt'] or res2['_start_dt'] > res1['_end_dt']:
            return False
        if not res1['_weekdays'].intersection(res2['_weekdays']):
            return False
        return max(res1['_s_sec'], res2['_s_sec']) < min(res1['_e_sec'], res2['_e_sec'])

    # ------------------------------------------------------------------ #
    #  Método principal                                                   #
    # ------------------------------------------------------------------ #

    @classmethod
    def allocate(cls, new_reservation, places, existing_reservations,
                 limit_moves=3):

        # print(f"new_reservation: {new_reservation}")
        # print(f"len(places): {len(places)}")    
        # print(f"places[0]: {places[0]}")
        # print(f"len(existing_reservations): {len(existing_reservations)}")    
        # print(f"existing_reservations[0]: {existing_reservations[0]}")
        
        # 1. Filtrar salas por tipos permitidos
        places = [
            p for p in places
            if p.get('data', {}).get('object_sub_type', [None])[0] in cls.ALLOWED_ROOM_TYPES
        ]
        if not places: return {"total_options": 0, "options": []}

        # 2. Normalizar reservas existentes (weekdays 1-indexed -> 0-indexed)
        valid_reservations = []
        for res in existing_reservations:
            rd = res.get('data', {})
            raw_wd = rd.get('weekdays')
            if isinstance(raw_wd, list):
                rd['weekdays'] = [int(w)-1 for w in raw_wd if str(w).strip().isdigit()]
            
            if cls._preparse_reservation(res):
                valid_reservations.append(res)

        # 3. Preparar nova_reserva
        new_res = {"id": "new_reservation_request", "data": new_reservation}
        raw_wd = new_res['data'].get('weekdays')
        if isinstance(raw_wd, list):
            new_res['data']['weekdays'] = [int(w)-1 for w in raw_wd if str(w).strip().isdigit()]
            
        if not cls._preparse_reservation(new_res):
            return {"total_options": 0, "options": []}

        cap_needed_new = cls.safe_int(new_res['data'].get('capacity_needed', 0))

        # 4. Mapear salas e buscar candidatas
        place_ids = [p['id'] for p in places]
        num_places = len(place_ids)
        place_names = [p['data'].get('number','') for p in places]
        place_caps = [cls.safe_int(p['data'].get('capacity', 0)) for p in places]
        place_id_to_idx = {pid: idx for idx, pid in enumerate(place_ids)}

        candidate_indices = [
            idx for idx in range(num_places)
            if place_caps[idx] >= cap_needed_new
        ]
        if not candidate_indices:
            return {"total_options": 0, "options": []}

        # 5. Indexar reservas e computar matriz local
        res_orig_j = {}
        res_capacities = {}
        for i, res in enumerate(valid_reservations):
            orig_pid = res.get('data', {}).get('place', [])
            if isinstance(orig_pid, list) and orig_pid and orig_pid[0] in place_id_to_idx:
                res_orig_j[i] = place_id_to_idx[orig_pid[0]]
            res_capacities[i] = cls.safe_int(res.get('data', {}).get('capacity_needed', 0))

        # Adjacência de conflitos
        conflict_adj = defaultdict(set)
        conflicts_with_new = set()

        for a in range(len(valid_reservations)):
            if cls._overlaps_fast(new_res, valid_reservations[a]):
                conflicts_with_new.add(a)
            for b in range(a + 1, len(valid_reservations)):
                if cls._overlaps_fast(valid_reservations[a], valid_reservations[b]):
                    conflict_adj[a].add(b)
                    conflict_adj[b].add(a)

        # 6. Solver
        results = []
        for target_j in candidate_indices:
            target_pid = place_ids[target_j]

            # Relevantes = conflitam com nova + alocados no target
            initial_relevant = set()
            initial_relevant.update(conflicts_with_new)
            for i, j in res_orig_j.items():
                if j == target_j:
                    initial_relevant.add(i)

            # Expandir exatamente 1 nível de conflito na rede toda
            relevant = set(initial_relevant)
            for ri in initial_relevant:
                relevant.update(conflict_adj[ri])

            relevant_list = sorted(relevant)
            local_n = 1 + len(relevant_list)
            
            def global_idx(li):
                return -1 if li == 0 else relevant_list[li-1]
            
            def global_res(li):
                return new_res if li == 0 else valid_reservations[global_idx(li)]

            # Computar conflitos restrito ao subgrupos (local_n x local_n)
            local_conflicts = []
            for a in range(local_n):
                for b in range(a + 1, local_n):
                    if a == 0:
                        if global_idx(b) in conflicts_with_new:
                            local_conflicts.append((a, b))
                    else:
                        if global_idx(b) in conflict_adj[global_idx(a)]:
                            local_conflicts.append((a, b))

            # Montar modelo
            model = cp_model.CpModel()
            x = {}
            for i in range(local_n):
                for j in range(num_places):
                    x[i, j] = model.NewBoolVar(f'x_{i}_{j}')

            # Restrição 1: Uma sala para cada reserva
            for i in range(local_n):
                model.AddExactlyOne(x[i, j] for j in range(num_places))

            # Restrição 2: Sem sobreposição
            for a, b in local_conflicts:
                for j in range(num_places):
                    model.Add(x[a, j] + x[b, j] <= 1)

            # Restrição 3: Capacidade
            for j in range(num_places):
                if place_caps[j] < cap_needed_new:
                    model.Add(x[0, j] == 0)

            for li in range(1, local_n):
                gi = global_idx(li)
                cap = res_capacities[gi]
                orig_j = res_orig_j.get(gi)
                if cap > 0:
                    for j in range(num_places):
                        if place_caps[j] < cap:
                            if orig_j != j:
                                model.Add(x[li, j] == 0)

            # Restrição 4: Fixar no candidato
            model.Add(x[0, target_j] == 1)

            # Remanejamentos
            is_moved = []
            for li in range(1, local_n):
                gi = global_idx(li)
                if gi in res_orig_j:
                    orig_j = res_orig_j[gi]
                    m = model.NewBoolVar(f'moved_{gi}')
                    model.Add(m == 1).OnlyEnforceIf(x[li, orig_j].Not())
                    model.Add(m == 0).OnlyEnforceIf(x[li, orig_j])
                    is_moved.append((li, gi, m))

            move_vars = [m for _, _, m in is_moved]
            model.Minimize(sum(move_vars))
            model.Add(sum(move_vars) <= limit_moves)

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 20.0
            status = solver.Solve(model)

            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                moves_detail = []
                for li, gi, m_var in is_moved:
                    if solver.Value(m_var):
                        for j in range(num_places):
                            if solver.Value(x[li, j]):
                                moves_detail.append({
                                    "reservation_id": valid_reservations[gi]['id'],
                                    "to_place": place_ids[j],
                                })
                                break

                results.append({
                    "place_id": target_pid,
                    "place_number": place_names[target_j],
                    "place_capacity": place_caps[target_j],
                    "moves_count": len(moves_detail),
                    "moves": moves_detail,
                    "solver_status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
                })

        # Sort por remanejamentos
        results.sort(key=lambda r: r['moves_count'])

        return {
            "total_options": len(results),
            "options": results,
            "solved_at": datetime.now(timezone.utc).isoformat(),
        }
