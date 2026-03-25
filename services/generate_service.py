"""
Service de geração de reservas semestrais.

Refatoração de gerenate-reservations.py em classe reutilizável.
Usa OR-Tools CP-SAT para otimizar alocação de disciplinas em salas.
"""

import re
import uuid
import unicodedata
from datetime import datetime, timedelta

from ortools.sat.python import cp_model


class GenerateService:
    """Gera grade completa de reservas para um semestre."""

    DEFAULT_SEMESTER_START = "2026-03-01"
    DEFAULT_SEMESTER_END = "2026-07-15"

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse_schedule(schedule_str):
        """
        Faz parse de strings como '13:30 15:30 (qui) 13:30 15:30 (sex)'.
        Retorna lista de dicts com day, start, end (minutos), strings.
        """
        if not schedule_str:
            return []

        days_map = {
            'dom': 0, 'seg': 1, 'ter': 2, 'qua': 3,
            'qui': 4, 'sex': 5, 'sáb': 6, 'sab': 6,
        }

        schedule_str = (
            schedule_str.lower()
            .replace('ç', 'c')
            .replace('ã', 'a')
            .replace('á', 'a')
        )

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
                    'end_str': end_time,
                })
        return slots

    @staticmethod
    def ranges_overlap(start1, end1, start2, end2):
        return max(start1, start2) < min(end1, end2)

    @classmethod
    def check_time_conflict(cls, slots1, slots2):
        """Retorna True se algum slot de slots1 sobrepõe algum de slots2."""
        for s1 in slots1:
            for s2 in slots2:
                if s1['day'] == s2['day']:
                    if cls.ranges_overlap(
                        s1['start'], s1['end'],
                        s2['start'], s2['end'],
                    ):
                        return True
        return False

    @staticmethod
    def clean_subject_name(name):
        """Normaliza string para comparação (remove acentos, lowercase)."""
        if not name:
            return ""
        nfkd_form = unicodedata.normalize('NFKD', name)
        return "".join(
            c for c in nfkd_form if not unicodedata.combining(c)
        ).lower()

    @staticmethod
    def parse_date(date_str):
        """Faz parse de datas nos formatos YYYY-MM-DD ou DD/MM/YYYY."""
        if not date_str:
            return None
        date_str = date_str.replace('\\', '')
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    @classmethod
    def generate_rrule(cls, start_date, end_date, weekday_idx, start_time_str):
        """Gera string RRULE para recorrência semanal."""
        rrule_days = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]
        day_code = rrule_days[weekday_idx]

        sem_start = (
            cls.parse_date(start_date)
            if isinstance(start_date, str)
            else start_date
        )
        sem_end = (
            cls.parse_date(end_date)
            if isinstance(end_date, str)
            else end_date
        )

        if not sem_start or not sem_end:
            return "", ""

        user_start_weekday = (sem_start.weekday() + 1) % 7
        days_ahead = weekday_idx - user_start_weekday
        if days_ahead < 0:
            days_ahead += 7
        first_occurrence = sem_start + timedelta(days=days_ahead)

        dtstart = (
            first_occurrence.strftime("%Y%m%d")
            + "T"
            + start_time_str.replace(":", "")
            + "00"
        )
        until = sem_end.strftime("%Y%m%d") + "T235959"

        rrule = (
            f"DTSTART:{dtstart}\\nRRULE:FREQ=WEEKLY;"
            f"INTERVAL=1;UNTIL={until};BYDAY={day_code}"
        )
        return rrule, first_occurrence.strftime("%Y-%m-%d")

    @staticmethod
    def index_of_reservation(new_subj, existing_subjects):
        """
        Verifica se já existe disciplina com mesmo código/horário
        mas grupo/ID diferente (para merge de turmas).
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

            if (
                ex_id != new_id
                and ex_group != new_group
                and ex_data.get('code') == new_code
                and ex_slots == new_slots
            ):
                return i
        return -1

    # ------------------------------------------------------------------ #
    #  Filtragem de disciplinas                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def _filter_subjects(cls, subjects_data):
        """Filtra e agrupa disciplinas válidas para alocação."""
        filtered = []
        skipped = {
            'vacancies_zero': 0,
            'vacancies_max': 0,
            'no_time': 0,
            'bad_format': 0,
            'estagio': 0,
            'monografia': 0,
            'practical_group': 0,
            'auto_res_disabled': 0,
        }

        for subj in subjects_data:
            sd = subj.get('data', {})

            # Vagas
            try:
                vacancies = int(sd.get('number_vacancies_offered', 0))
            except (ValueError, TypeError):
                vacancies = 0

            if vacancies <= 0:
                skipped['vacancies_zero'] += 1
                continue
            if vacancies >= 80:
                skipped['vacancies_max'] += 1
                continue

            # Horário
            desired_time = sd.get('desired_time', '')
            if not desired_time:
                skipped['no_time'] += 1
                continue

            slots = cls.parse_schedule(desired_time)
            if not slots:
                skipped['bad_format'] += 1
                continue

            # Nome
            name_clean = cls.clean_subject_name(
                sd.get('name_of_subject', '')
            )
            if 'estagio' in name_clean:
                skipped['estagio'] += 1
                continue
            if 'monografia' in name_clean:
                skipped['monografia'] += 1
                continue

            # Grupo prático
            group = str(sd.get('group', '')).upper()
            if 'P' in group:
                skipped['practical_group'] += 1
                continue

            # Auto-reserva habilitada
            use_auto = sd.get('use_on_auto_reservation', [])
            is_sim = (
                isinstance(use_auto, list)
                and len(use_auto) > 0
                and str(use_auto[0]).upper() == 'SIM'
            )
            if not is_sim:
                skipped['auto_res_disabled'] += 1
                continue

            # Preparar para alocação
            subj['parsed_slots'] = slots
            subj['vacancies_int'] = vacancies
            subj['group_list'] = [str(sd.get('group', ''))]
            subj['id_list'] = [subj['id']]

            # Merge de turmas com mesmo horário/código
            match_idx = cls.index_of_reservation(subj, filtered)
            if match_idx != -1:
                existing = filtered[match_idx]
                existing['vacancies_int'] += vacancies
                existing['group_list'].append(str(sd.get('group', '')))
                existing['id_list'].append(subj['id'])
            else:
                filtered.append(subj)

        return filtered, skipped

    # ------------------------------------------------------------------ #
    #  Método principal                                                   #
    # ------------------------------------------------------------------ #

    @classmethod
    def generate(cls, subjects, places, semester_start=None,
                 semester_end=None):
        """
        Gera reservas semestrais otimizadas via CP-SAT.

        Args:
            subjects: lista de disciplinas (formato JSON da intranet).
            places: lista de locais/salas disponíveis.
            semester_start: data início (YYYY-MM-DD). Padrão: 2026-03-01.
            semester_end: data fim (YYYY-MM-DD). Padrão: 2026-07-15.

        Returns:
            dict com 'reservations' e 'stats'.

        Raises:
            ValueError: se não for possível encontrar solução viável.
        """
        semester_start = semester_start or cls.DEFAULT_SEMESTER_START
        semester_end = semester_end or cls.DEFAULT_SEMESTER_END

        # Filtrar disciplinas
        filtered_subjects, skipped = cls._filter_subjects(subjects)

        if not filtered_subjects:
            return {
                "reservations": [],
                "stats": {
                    "total_reservations": 0,
                    "subjects_accepted": 0,
                    "subjects_skipped": skipped,
                    "success_rate": 100.0,
                },
            }

        # Modelo CP-SAT
        model = cp_model.CpModel()
        allocations = {}

        # Pares conflitantes
        conflicting_pairs = []
        for i in range(len(filtered_subjects)):
            for j in range(i + 1, len(filtered_subjects)):
                if cls.check_time_conflict(
                    filtered_subjects[i]['parsed_slots'],
                    filtered_subjects[j]['parsed_slots'],
                ):
                    conflicting_pairs.append((i, j))

        # Variáveis de alocação (somente onde capacidade permite)
        for s_idx, s in enumerate(filtered_subjects):
            vacancies = s['vacancies_int']
            for p_idx, p in enumerate(places):
                try:
                    capacity = int(p['data']['capacity'])
                except (ValueError, TypeError, KeyError):
                    capacity = 0

                if vacancies <= capacity:
                    var_name = f'alloc_s{s_idx}_p{p_idx}'
                    allocations[(s_idx, p_idx)] = model.NewBoolVar(var_name)

        # Restrições: cada disciplina em no máximo 1 sala
        total_assigned = []
        for s_idx in range(len(filtered_subjects)):
            pk_vars = [
                allocations[(s_idx, p_idx)]
                for p_idx in range(len(places))
                if (s_idx, p_idx) in allocations
            ]
            if pk_vars:
                model.Add(sum(pk_vars) <= 1)
                total_assigned.append(sum(pk_vars))

        # Restrições de conflito
        for s1_idx, s2_idx in conflicting_pairs:
            for p_idx in range(len(places)):
                if ((s1_idx, p_idx) in allocations
                        and (s2_idx, p_idx) in allocations):
                    model.Add(
                        allocations[(s1_idx, p_idx)]
                        + allocations[(s2_idx, p_idx)]
                        <= 1
                    )

        # Objetivo: maximizar disciplinas alocadas
        model.Maximize(sum(total_assigned))

        # Forçar 100% de alocação
        model.Add(sum(total_assigned) == len(filtered_subjects))

        # Resolver
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 300.0
        solver.parameters.num_search_workers = 8
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise ValueError(
                "Não foi possível encontrar solução viável para "
                "alocação das disciplinas."
            )

        # Gerar objetos de reserva
        reservations = []
        assigned_count = 0
        unassigned = []

        for s_idx in range(len(filtered_subjects)):
            assigned_p_idx = -1
            for p_idx in range(len(places)):
                if (s_idx, p_idx) in allocations:
                    if solver.Value(allocations[(s_idx, p_idx)]) == 1:
                        assigned_p_idx = p_idx
                        break

            if assigned_p_idx != -1:
                assigned_count += 1
                subj = filtered_subjects[s_idx]
                place = places[assigned_p_idx]
                subj_data = subj.get('data', {})

                start_date = (
                    subj_data.get('desired_start_date')
                    or semester_start
                )
                end_date = (
                    subj_data.get('desired_end_date')
                    or semester_end
                )

                for slot in subj['parsed_slots']:
                    rrule_str, start_date_actual = cls.generate_rrule(
                        start_date, end_date,
                        slot['day'], slot['start_str'],
                    )

                    dur_minutes = slot['end'] - slot['start']
                    dur_h = dur_minutes // 60
                    dur_m = dur_minutes % 60
                    duration_str = f"{dur_h:02d}:{dur_m:02d}"

                    groups_str = " / ".join(subj['group_list'])
                    title_desc = f"{subj['data']['code']} ({groups_str})"

                    end_date_str = (
                        end_date
                        if isinstance(end_date, str)
                        else end_date.strftime("%Y-%m-%d")
                    )

                    res_obj = {
                        "id": str(uuid.uuid4()).replace('-', '')[:20],
                        "data": {
                            "date": start_date_actual,
                            "desc": title_desc,
                            "place": [place['id']],
                            "rrule": rrule_str,
                            "title": title_desc,
                            "duration": duration_str,
                            "end_date": end_date_str,
                            "end_time": slot['end_str'],
                            "weekdays": [slot['day']],
                            "applicant": "",
                            "frequency": ["weekly"],
                            "start_time": slot['start_str'],
                            "class_subject": subj['id_list'],
                        },
                        "form_id": "-2",
                        "object_name": "reservation",
                        "is_active": "1",
                        "owner": "",
                        "group_owner": "py",
                        "permissions": "777",
                        "remote_ip": "127.0.0.1",
                        "updated_at": datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "created_at": datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    }
                    reservations.append(res_obj)
            else:
                subj = filtered_subjects[s_idx]
                unassigned.append(
                    f"{subj['data']['code']} "
                    f"({subj['data']['group']}) - "
                    f"Vagas: {subj['vacancies_int']}"
                )

        total_subjects = len(filtered_subjects)
        success_rate = (
            (assigned_count / total_subjects * 100)
            if total_subjects > 0
            else 100.0
        )

        return {
            "reservations": reservations,
            "stats": {
                "total_reservations": len(reservations),
                "subjects_accepted": total_subjects,
                "subjects_assigned": assigned_count,
                "subjects_skipped": skipped,
                "unassigned": unassigned,
                "success_rate": round(success_rate, 2),
            },
        }
