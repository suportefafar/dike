# Documentação da API Dike

A API Dike fornece serviços para geração e alocação de reservas de salas, otimizando o uso de espaços com base em disciplinas e horários. Utiliza o solver CP-SAT (Google OR-Tools) para encontrar soluções otimizadas.

---

## 1. Health Check
Verifica se o serviço está operando corretamente.

- **Método:** `GET`
- **URL:** `/api/health`
- **Resposta:**
  ```json
  { "status": "ok" }
  ```

---

## 2. Gerar Reservas (`/api/generate`)
Gera uma grade completa de reservas para um semestre. O serviço filtra automaticamente as disciplinas com base em critérios de elegibilidade.

- **Método:** `POST`
- **URL:** `/api/generate`
- **Payload (`JSON`):**
  | Campo | Tipo | Obrigatório | Descrição |
  | :--- | :--- | :---: | :--- |
  | `subjects` | `list[object]` | Sim | Lista de disciplinas (ver [Estrutura de Disciplinas](#estrutura-de-disciplinas)). |
  | `places` | `list[object]` | Sim | Lista de locais (ver [Estrutura de Locais](#estrutura-de-locais)). |
  | `semester_start` | `string` | Não | Início do semestre (`YYYY-MM-DD`). Padrão: `2026-03-01`. |
  | `semester_end` | `string` | Não | Fim do semestre (`YYYY-MM-DD`). Padrão: `2026-07-15`. |

### Estrutura de Disciplinas (`subjects`)
Cada item da lista `subjects` deve seguir o formato:
```json
{
  "id": "12345",
  "data": {
    "code": "CIC0001",
    "name_of_subject": "Algoritmos e Programação",
    "number_vacancies_offered": 40,
    "desired_time": "13:30 15:30 (qui) 13:30 15:30 (sex)",
    "group": "A",
    "use_on_auto_reservation": ["SIM"],
    "desired_start_date": "2026-03-01",
    "desired_end_date": "2026-07-15"
  }
}
```

### Estrutura de Locais (`places`)
Cada item da lista `places` deve seguir o formato:
```json
{
  "id": "R1",
  "data": {
    "capacity": 50,
    "number": "Sala 101",
    "object_sub_type": ["classroom"]
  }
}
```
> [!NOTE]
> Os tipos de sala permitidos são: `classroom`, `living_room`, `computer_lab` e `multimedia_room`.


### Critérios de Filtragem (Skipped Subjects)
Disciplinas são ignoradas se:
- Possuem 0 ou mais de 80 vagas.
- Não possuem horário definido ou formato de horário inválido.
- Contém "estágio" ou "monografia" no nome.
- Pertencem ao grupo prático (ex: "P1").
- A flag `use_on_auto_reservation` não é "SIM".

- **Exemplo de Resposta:**
  ```json
  {
    "reservations": [...],
    "stats": {
      "total_reservations": 150,
      "subjects_accepted": 80,
      "subjects_assigned": 80,
      "subjects_skipped": {
        "vacancies_zero": 5,
        "no_time": 10,
        "auto_res_disabled": 20
      },
      "unassigned": [],
      "success_rate": 100.0
    }
  }
  ```

---

## 3. Alocar/Sugestão de Vaga (`/api/allocate`)
Busca opções de alocação para uma nova reserva, minimizando o número de remanejamentos de reservas existentes.

- **Método:** `POST`
- **URL:** `/api/allocate`
- **Payload (`JSON`):**
  | Campo | Tipo | Obrigatório | Descrição |
  | :--- | :--- | :---: | :--- |
  | `new_reservation` | `object` | Sim | Dados da nova reserva (**estrutura flat**, ver abaixo). |
  | `places` | `list[object]` | Sim | Lista de locais (ver [Estrutura de Locais](#estrutura-de-locais)). |
  | `existing_reservations`| `list[object]` | Sim | Reservas atuais (ver [Estrutura de Reservas Existentes](#estrutura-de-reservas-existentes)). |
  | `limit_moves` | `int` | Não | Máximo de remanejamentos (Padrão: 3). |

### Estrutura de Nova Reserva (`new_reservation`)
```json
{
  "title": "Aula Extra - CIC0001",
  "date": "2026-04-10",
  "start_time": "14:00",
  "end_time": "16:00",
  "capacity_needed": 30,
  "weekdays": [5],
  "frequency": "weekly",
  "end_date": "2026-07-15"
}
```

### Estrutura de Reservas Existentes (`existing_reservations`)
```json
{
  "id": "1772117841001f087e0a",
  "data": {
    "title": "Cálculo 1",
    "date": "2026-03-01",
    "start_time": "08:00",
    "end_time": "10:00",
    "capacity_needed": 45,
    "weekdays": [1, 3],
    "place": ["1772117840015436d2bc"],
    "frequency": "weekly",
    "end_date": "2026-07-15"
  }
}
```

### Estrutura de Locais (`places`)
```json
{
  "id": "1772117840015436d2bc",
  "data": {
    "capacity": 50,
    "number": "Sala 101",
    "object_sub_type": ["classroom"]
  }
}
```

> [!IMPORTANT]
> A capacidade necessária para cada reserva é obtida diretamente do campo `capacity_needed`.


- **Exemplo de Resposta (Sucesso):**
  ```json
  {
    "total_options": 2,
    "options": [
      {
        "place_id": "R2",
        "place_number": "102",
        "place_capacity": 80,
        "moves_count": 0,
        "moves": [],
        "solver_status": "OPTIMAL"
      },
      {
        "place_id": "R1",
        "place_number": "101",
        "place_capacity": 50,
        "moves_count": 1,
        "moves": [
          {
            "reservation_id": "E1",
            "to_place": "R2"
          }
        ],
        "solver_status": "OPTIMAL"
      }
    ],
    "solved_at": "2026-04-07T14:00:00+00:00"
  }
  ```

---

## 4. Detalhes Técnicos
A API Dike utiliza o solver **CP-SAT** do Google OR-Tools para resolver problemas de alocação de restrições.

- **Otimização de Alocação (`/api/allocate`):** O solver busca minimizar o número de remanejamentos necessários, expandindo o escopo apenas para reservas que conflitam diretamente com a nova solicitação ou com as salas afetadas.
- **Geração Semestral (`/api/generate`):** Maximiza o número de disciplinas alocadas, respeitando restrições de capacidade e evitando conflitos de horários em salas comuns. Faz merge automático de turmas com mesmo código e horário.

---

## 5. Execução com Docker

Para rodar a API Dike utilizando Docker Compose:

1. Certifique-se de que o Docker e o Docker Compose estão instalados.
2. No diretório raiz do projeto, execute:
   ```bash
   docker-compose up --build
   ```
3. A API estará disponível em `http://localhost:3002`.

---

## Erros Comuns
- `400 Bad Request`: JSON inválido ou campos obrigatórios ausentes.
- `404 Not Found`: Endpoint não encontrado.
- `422 Unprocessable Entity`: Erro de validação ou impossibilidade de encontrar solução viável para a geração.
- `500 Internal Server Error`: Erro interno no processamento dos dados.