import json
import os
from datetime import datetime
from ortools.sat.python import cp_model

# =============================================================================
# NOVA RESERVA DESEJADA (Simulação de requisição de professor)
# =============================================================================
NEW_RESERVATION_DATA = {
    "id": "prof_req_2026_conf",
    "data": {
        "title": "Aula Magna de Farmácia (LOTADA)",
        "desc": "FAF-MAGNA",
        "date": "2026-03-02",
        "end_date": "2026-07-15",
        "start_time": "14:00",
        "end_time": "17:00",
        "weekdays": [0],              # Segunda-feira à noite
        "capacity": 70,        # Exige sala grande
        "is_weekly": True
    }
}

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def time_to_seconds(t_str):
    h, m = map(int, t_str.split(':'))
    return h * 3600 + m * 60

def overlaps(res1, res2):
    """
    Verifica sobreposição entre duas reservas (considerando semanal vs único).
    """
    d1 = res1['data']
    d2 = res2['data']
    
    # 1. Intervalo de Datas
    start1 = datetime.strptime(d1['date'], '%Y-%m-%d')
    end1 = datetime.strptime(d1.get('end_date', d1['date']), '%Y-%m-%d')
    start2 = datetime.strptime(d2['date'], '%Y-%m-%d')
    end2 = datetime.strptime(d2.get('end_date', d2['date']), '%Y-%m-%d')
    
    if start1 > end2 or start2 > end1:
        return False
        
    # 2. Dias da semana (apenas se ambos ou ao menos um for semanal)
    # Se um é único, ele tem uma data específica. Verificamos se essa data 
    # cai em um dia que o outro (semanal) ocupa.
    
    is_w1 = d1.get('is_weekly', True) # Assume semanal se não especificado (padrão do arquivo)
    is_w2 = d2.get('is_weekly', True)
    
    w1 = set(d1.get('weekdays', []))
    w2 = set(d2.get('weekdays', []))
    
    # Se não houver dia da semana definido (ex: feriado ou log de erro), tenta inferir da data
    if not w1: w1 = {start1.weekday()}
    if not w2: w2 = {start2.weekday()}

    if not w1.intersection(w2):
        return False
    
    # 3. Horários
    s1 = time_to_seconds(d1['start_time'])
    e1 = time_to_seconds(d1['end_time'])
    s2 = time_to_seconds(d2['start_time'])
    e2 = time_to_seconds(d2['end_time'])
    
    return max(s1, s2) < min(e1, e2)

def safe_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(value)) # float handle things like "20.0"
    except (ValueError, TypeError):
        return default

def solve_allocation(limit_moves=3):
    places = load_json('places.json')
    existing_reservations = load_json('reservations.json')
    subjects = load_json('class_subjects_all_fourth_pre.json')
    subject_cap = {s['id']: safe_int(s['data'].get('number_vacancies_offered')) for s in subjects}

    # Requisitos da nova reserva
    cap_needed_new = safe_int(NEW_RESERVATION_DATA['data'].get('capacity'))
    for sid in NEW_RESERVATION_DATA['data'].get('class_subject', []):
        cap_needed_new = max(cap_needed_new, subject_cap.get(sid, 0))
    
    all_places = {p['id']: p for p in places}
    place_ids = list(all_places.keys())

    # 1. Filtrar Salas Candidatas (por capacidade)
    candidate_pids = [pid for pid in place_ids if safe_int(all_places[pid]['data'].get('capacity')) >= cap_needed_new]
    
    print(f"\n[PESQUISA] Analisando {len(candidate_pids)} salas candidatas (Capacidade >= {cap_needed_new})...")
    
    results = []
    
    for target_pid in candidate_pids:
        target_idx = place_ids.index(target_pid)
        place_info = all_places[target_pid]['data']
        
        # Solver por sala
        model = cp_model.CpModel()
        req_to_solve = [NEW_RESERVATION_DATA] + existing_reservations
        
        x = {}
        for i in range(len(req_to_solve)):
            for j in range(len(place_ids)):
                x[i, j] = model.NewBoolVar(f'x_{i}_{j}')
        
        # Restrições básicas
        for i in range(len(req_to_solve)):
            model.Add(sum(x[i, j] for j in range(len(place_ids))) == 1)
            
        for i in range(len(req_to_solve)):
            for k in range(i + 1, len(req_to_solve)):
                if overlaps(req_to_solve[i], req_to_solve[k]):
                    for j in range(len(place_ids)):
                        model.Add(x[i, j] + x[k, j] <= 1)

        # Restrição de Capacidade (Geral)
        for i, req in enumerate(req_to_solve):
            c_needed = safe_int(req['data'].get('capacity', 0))
            for sid in req['data'].get('class_subject', []):
                c_needed = max(c_needed, subject_cap.get(sid, 0))
                
            if c_needed > 0:
                for j, pid in enumerate(place_ids):
                    if safe_int(all_places[pid]['data'].get('capacity')) < c_needed:
                        model.Add(x[i, j] == 0)

        # FIXAR NOVA RESERVA NESTA SALA
        model.Add(x[0, target_idx] == 1)

        # Variáveis de Remanejamento
        is_moved = []
        for i in range(1, len(req_to_solve)):
            orig_place_ids = req_to_solve[i]['data'].get('place', [])
            orig_place_id = orig_place_ids[0] if orig_place_ids else None
            
            if orig_place_id and orig_place_id in place_ids:
                orig_idx = place_ids.index(orig_place_id)
                m = model.NewBoolVar(f'moved_{i}_{target_pid}')
                model.Add(m == 1).OnlyEnforceIf(x[i, orig_idx].Not())
                model.Add(m == 0).OnlyEnforceIf(x[i, orig_idx])
                is_moved.append(m)
            else:
                is_moved.append(model.NewConstant(0))

        model.Minimize(sum(is_moved))
        model.Add(sum(is_moved) <= limit_moves)
        
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 2.0 # Timeout rápido por sala
        status = solver.Solve(model)
        
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            num_moves = sum(int(solver.Value(m)) for m in is_moved)
            moves_detail = []
            for i in range(1, len(req_to_solve)):
                orig_ids = req_to_solve[i]['data'].get('place', [])
                orig_id = orig_ids[0] if orig_ids else None
                for j, pid in enumerate(place_ids):
                    if solver.Value(x[i, j]):
                        if pid != orig_id:
                            moves_detail.append({
                                "req": req_to_solve[i]['data'].get('title', 'Reserva'),
                                "from": all_places[orig_id]['data']['number'] if orig_id else "N/A",
                                "to": all_places[pid]['data']['number']
                            })
            
            results.append({
                "pid": target_pid,
                "number": place_info['number'],
                "capacity": place_info['capacity'],
                "moves_count": num_moves,
                "moves": moves_detail
            })

    # Ordenar resultados por número de remanejamentos
    results.sort(key=lambda x: x['moves_count'])

    if results:
        print("\n=== OPÇÕES DE ALOCAÇÃO ENCONTRADAS ===")
        for res in results:
            prefix = "[DIRETA]" if res['moves_count'] == 0 else f"[{res['moves_count']} REMANEJ.]"
            print(f"\n{prefix} Sala {res['number']} (Capacidade: {res['capacity']})")
            if res['moves']:
                for m in res['moves']:
                    print(f"   - {m['req']}: de {m['from']} para {m['to']}")
            else:
                print("   - Nenhuma alteração necessária.")
    else:
        print("\n!!! NENHUMA SALA CONSEGUIU SUPORTAR A RESERVA (mesmo com remanejamento limitado) !!!")

if __name__ == "__main__":
    solve_allocation(limit_moves=3)
