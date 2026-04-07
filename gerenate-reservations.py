import json
import re
from datetime import datetime, timedelta
from ortools.sat.python import cp_model
import uuid

SEMESTER_START = "2026-03-01" 
SEMESTER_END = "2026-07-15"   
OUTPUT_FILE = "reservations.json"

def parse_schedule(schedule_str):
    """
    Parses schedule strings like "13:30 15:30 (qui) 13:30 15:30 (sex)"
    Returns a list of dicts: {'day': int (0=Mon), 'start': int (mins), 'end': int (mins), 'day_str': str}
    """
    if not schedule_str:
        return []
    
    days_map = {
        'dom': 0, 'seg': 1, 'ter': 2, 'qua': 3, 'qui': 4, 'sex': 5, 'sáb': 6, 'sab': 6
    }
    
    schedule_str = schedule_str.lower().replace('ç', 'c').replace('ã', 'a').replace('á', 'a')
    
    pattern = r"(\d{2}:\d{2})\s+(\d{2}:\d{2})\s+\((\w{3})\)"
    matches = re.findall(pattern, schedule_str)
    
    slots = []
    for start_time, end_time, day_abbr in matches:
        if day_abbr in days_map:
            h1, m1 = map(int, start_time.split(':'))
            h2, m2 = map(int, end_time.split(':'))
            slots.append({
                'day': days_map[day_abbr],
                'day_str': day_abbr,
                'start': h1 * 60 + m1,
                'end': h2 * 60 + m2,
                'start_str': start_time,
                'end_str': end_time
            })
    return slots

def ranges_overlap(start1, end1, start2, end2):
    return max(start1, start2) < min(end1, end2)

def check_time_conflict(slots1, slots2):
    """Returns True if any slot in slots1 overlaps with any slot in slots2."""
    for s1 in slots1:
        for s2 in slots2:
            if s1['day'] == s2['day']:
            
                if ranges_overlap(s1['start'], s1['end'], s2['start'], s2['end']):
                    return True
    return False

def clean_subject_name(name):
    """Normalize string for comparison (remove accents, lowercase)."""
    if not name: return ""
    import unicodedata
    nfkd_form = unicodedata.normalize('NFKD', name)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()

def parse_date(date_str):
    """
    Parses date strings in YYYY-MM-DD or DD/MM/YYYY formats.
    """
    if not date_str:
        return None
    date_str = date_str.replace('\\', '') # Handle escaped slashes if any
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None

def generate_rrule(start_date, end_date, weekday_idx, start_time_str):
    """
    Generates RRULE string.
    start_date and end_date can be strings or datetime objects.
    """

    rrule_days = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]
    day_code = rrule_days[weekday_idx]
    
    if isinstance(start_date, str):
        sem_start = parse_date(start_date)
    else:
        sem_start = start_date
        
    if isinstance(end_date, str):
        sem_end = parse_date(end_date)
    else:
        sem_end = end_date
    
    if not sem_start or not sem_end:
        return "", ""
    

    # sem_start.weekday() is 0 for Monday, 6 for Sunday.
    # Convert to 0 for Sunday, 1 for Monday, etc.
    user_start_weekday = (sem_start.weekday() + 1) % 7
    
    days_ahead = weekday_idx - user_start_weekday
    if days_ahead < 0:
        days_ahead += 7
    first_occurrence = sem_start + timedelta(days=days_ahead)
    
    dtstart = first_occurrence.strftime("%Y%m%d") + "T" + start_time_str.replace(":", "") + "00"
    until = sem_end.strftime("%Y%m%d") + "T235959"
    
    return f"DTSTART:{dtstart}\\nRRULE:FREQ=WEEKLY;INTERVAL=1;UNTIL={until};BYDAY={day_code}", first_occurrence.strftime("%Y-%m-%d")


print("Loading data...")
try:
    with open('class_subjects.json', 'r') as f:
        subjects_data = json.load(f)
    with open('places.json', 'r') as f:
        places_data = json.load(f)
except FileNotFoundError:
    print("Error: Input files (class_subjects.json, places.json) not found.")
    exit(1)

print(f"Loaded {len(subjects_data)} subjects and {len(places_data)} places.")

filtered_subjects = []
skipped_counts = {
    'vacancies_zero': 0,
    'vacancies_max': 0,
    'no_time': 0,
    'bad_format': 0,
    'estagio': 0,
    'monografia': 0,
    'practical_group': 0,
    'auto_res_disabled': 0
}

def index_of_reservation(new_subj, existing_subjects):
    """
    Python implementation of the PHP index_of_reservation logic.
    Checks if a matching subject (same code/schedule, different group/ID) already exists.
    """
    new_data = new_subj.get('data', {})
    new_code = new_data.get('code')
    new_group = str(new_data.get('group', ''))
    new_slots = new_subj.get('parsed_slots', [])
    new_id = new_subj.get('id')

    for i, existing in enumerate(existing_subjects):
        ex_data = existing.get('data', {})
        ex_slots = existing.get('parsed_slots', [])
        ex_group = str(ex_data.get('group', ''))
        ex_id = existing.get('id')

        # Logic comparison based on PHP function:
        # Different ID AND Different Group AND Same Code AND Same Schedule (start, end, weekdays)
        if (ex_id != new_id and 
            ex_group != new_group and 
            ex_data.get('code') == new_code and 
            ex_slots == new_slots):
            return i
    return -1

for subj in subjects_data:
    sd = subj.get('data', {})
    

    try:
        vacancies = int(sd.get('number_vacancies_offered', 0))
    except ValueError:
        vacancies = 0

    if vacancies <= 0:
        skipped_counts['vacancies_zero'] += 1
        continue
    if vacancies >= 80:
        skipped_counts['vacancies_max'] += 1
        continue
        

    desired_time = sd.get('desired_time', '')
    if not desired_time:
        skipped_counts['no_time'] += 1
        continue
        
    slots = parse_schedule(desired_time)
    if len(slots) <= 0:
        skipped_counts['bad_format'] += 1
        continue
        

    name_clean = clean_subject_name(sd.get('name_of_subject', ''))
    if 'estagio' in name_clean:
        skipped_counts['estagio'] += 1
        continue
    if 'monografia' in name_clean:
        skipped_counts['monografia'] += 1
        continue
        

    group = str(sd.get('group', '')).upper()
    if 'P' in group:
        skipped_counts['practical_group'] += 1
        continue
        

    use_auto = sd.get('use_on_auto_reservation', [])
    # Ensure it's a list and has at least one element "SIM"
    is_sim = isinstance(use_auto, list) and len(use_auto) > 0 and str(use_auto[0]).upper() == 'SIM'
    
    if not is_sim:
        skipped_counts['auto_res_disabled'] += 1
        continue
            

    subj['parsed_slots'] = slots
    subj['vacancies_int'] = vacancies
    subj['group_list'] = [str(sd.get('group', ''))]
    subj['id_list'] = [subj['id']]
    
    # Check for existing subject to merge
    match_idx = index_of_reservation(subj, filtered_subjects)
    if match_idx != -1:
        existing = filtered_subjects[match_idx]
        existing['vacancies_int'] += vacancies
        existing['group_list'].append(str(sd.get('group', '')))
        existing['id_list'].append(subj['id'])
    else:
        filtered_subjects.append(subj)

print("\nFiltering Report:")
print(f"Accepted subjects: {len(filtered_subjects)}")
print(f"Skipped: {json.dumps(skipped_counts, indent=2)}")

print("\nSetting up CP Model...")
model = cp_model.CpModel()
allocations = {}

print("Calculating conflicts...")
conflicting_pairs = []
for i in range(len(filtered_subjects)):
    for j in range(i + 1, len(filtered_subjects)):
        if check_time_conflict(filtered_subjects[i]['parsed_slots'], filtered_subjects[j]['parsed_slots']):
            conflicting_pairs.append((i, j))

print(f"Found {len(conflicting_pairs)} conflicting subject pairs.")

valid_assignments = [] 

for s_idx, s in enumerate(filtered_subjects):
    vacancies = s['vacancies_int']
    for p_idx, p in enumerate(places_data):
        try:
            capacity = int(p['data']['capacity'])
        except ValueError:
            capacity = 0
        
        if vacancies <= capacity:
            var_name = f'alloc_s{s_idx}_p{p_idx}'
            allocations[(s_idx, p_idx)] = model.NewBoolVar(var_name)
            valid_assignments.append((s_idx, p_idx))

print(f"Created {len(allocations)} allocation variables.")

total_assigned = []

for s_idx in range(len(filtered_subjects)):
    pk_vars = [allocations[(s_idx, p_idx)] for p_idx in range(len(places_data)) 
               if (s_idx, p_idx) in allocations]
    
    if pk_vars:
        model.Add(sum(pk_vars) <= 1)
        total_assigned.append(sum(pk_vars))
    else:
        print(f"Warning: Subject {filtered_subjects[s_idx]['data']['code']} (Vacancies: {filtered_subjects[s_idx]['vacancies_int']}) fits no room due to capacity!")

# Conflict constraints
for s1_idx, s2_idx in conflicting_pairs:
    for p_idx in range(len(places_data)):
        if (s1_idx, p_idx) in allocations and (s2_idx, p_idx) in allocations:
            model.Add(allocations[(s1_idx, p_idx)] + allocations[(s2_idx, p_idx)] <= 1)

# Objective: Maximize assigned subjects
model.Maximize(sum(total_assigned))

# Enforce that ALL subjects must be assigned (100% allocation)
model.Add(sum(total_assigned) == len(filtered_subjects))

print("Solving...")
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 300.0  # 5 minutes
solver.parameters.num_search_workers = 8
status = solver.Solve(model)

print(f"Status: {solver.StatusName(status)}")
print(f"Objective Value: {solver.ObjectiveValue()} / {len(filtered_subjects)}")

reservations = []

if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
    print("Generating reservations...")
    
    assigned_count = 0
    unassigned = []
    for s_idx in range(len(filtered_subjects)):
        assigned_p_idx = -1
    
        for p_idx in range(len(places_data)):
            if (s_idx, p_idx) in allocations:
                if solver.Value(allocations[(s_idx, p_idx)]) == 1:
                    assigned_p_idx = p_idx
                    break
        
        if assigned_p_idx != -1:
            assigned_count += 1
            subj = filtered_subjects[s_idx]
            place = places_data[assigned_p_idx]
            
            subj_data = subj.get('data', {})
            start_date = (subj_data.get('desired_start_date') 
                          if subj_data.get('desired_start_date') else SEMESTER_START)
            end_date = (subj_data.get('desired_end_date') 
                        if subj_data.get('desired_end_date') else SEMESTER_END)

            for slot in subj['parsed_slots']:
            
                rrule_str, start_date_actual = generate_rrule(
                    start_date, 
                    end_date, 
                    slot['day'], 
                    slot['start_str']
                )
                
            
                dur_minutes = slot['end'] - slot['start']
                dur_h = dur_minutes // 60
                dur_m = dur_minutes % 60
                duration_str = f"{dur_h:02d}:{dur_m:02d}"
                
                groups_str = " / ".join(subj['group_list'])
                title_desc = f"{subj['data']['code']} ({groups_str})"
                
                res_obj = {
                    "id": str(uuid.uuid4()).replace('-', '')[:20],
                    "data": {
                        "date": start_date_actual,
                        "desc": title_desc,
                        "place": [place['id']],
                        "rrule": rrule_str,
                        "title": title_desc,
                        "duration": duration_str,
                        "end_date": end_date if isinstance(end_date, str) else end_date.strftime("%Y-%m-%d"),
                        "end_time": slot['end_str'],
                        "weekdays": [slot['day']],
                        "applicant": '',
                        "frequency": ["weekly"],
                        "start_time": slot['start_str'],
                        "class_subject": subj['id_list']
                    },
                    "form_id": "-2",
                    "object_name": "reservation",
                    "is_active": "1",
                    "owner": '',
                    "group_owner": "py",
                    "permissions": "777",
                    "remote_ip": "127.0.0.1",
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                reservations.append(res_obj)
        else:
            subj = filtered_subjects[s_idx]
            unassigned.append(f"{subj['data']['code']} ({subj['data']['group']}) - Vacancies: {subj['vacancies_int']}")

    if unassigned:
        print("\nUnassigned Subjects:")
        for item in unassigned:
            print(f"- {item}")

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(reservations, f, indent=2)
    print(f"Successfully generated {len(reservations)} reservations in {OUTPUT_FILE}.")
    
else:
    print("Could not find a feasible solution.")

