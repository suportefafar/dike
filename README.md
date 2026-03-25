# Documentação da API Dike

A API Dike fornece serviços para geração e alocação de reservas de salas, otimizando o uso de espaços com base em disciplinas e horários.

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
Gera uma grade completa de reservas para um semestre.

- **Método:** `POST`
- **URL:** `/api/generate`
- **Payload:**
  | Campo | Tipo | Obrigatório | Descrição |
  | :--- | :--- | :---: | :--- |
  | `subjects` | `list` | Sim | Disciplinas com horários e necessidades. |
  | `places` | `list` | Sim | Locais disponíveis e capacidades. |
  | `semester_start` | `string` | Não | Início (YYYY-MM-DD). Padrão: `2026-03-01`. |
  | `semester_end` | `string` | Não | Fim (YYYY-MM-DD). Padrão: `2026-07-15`. |

- **Exemplo de Resposta:**
  ```json
  {
    "reservations": [...],
    "stats": { "total_reservations": 150, "success_rate": 98.5 }
  }
  ```

---

## 3. Alocar/Sugestão de Vaga (`/api/allocate`)
Busca opções de alocação para uma nova reserva, permitindo pequenos remanejamentos se necessário.

- **Método:** `POST`
- **URL:** `/api/allocate`
- **Payload:**
  | Campo | Tipo | Obrigatório | Descrição |
  | :--- | :--- | :---: | :--- |
  | `new_reservation` | `dict` | Sim | Dados da nova reserva (**estrutura flat**). |
  | `places` | `list` | Sim | Lista de locais (**estrutura com chave `data`**). |
  | `existing_reservations`| `list` | Sim | Reservas existentes (**estrutura com chave `data`**). |
  | `subjects` | `list` | Não | Disciplinas com `number_vacancies_offered` em `data`. Usadas para inferir capacidade mínima da sala quando a reserva possui `class_subject`. |
  | `limit_moves` | `int` | Não | Limite de mudanças permitidas (Padrão: 3). |

- **Exemplo de Payload:**
  ```json
  {
    "new_reservation": {
      "title": "Nova Aula de Química",
      "date": "2026-03-02",
      "start_time": "14:00",
      "end_time": "16:00",
      "capacity": 40,
      "class_subject": ["DISC-001"]
    },
    "places": [
      {
        "id": "R1",
        "data": { "number": "101", "capacity": 50 }
      },
      {
        "id": "R2",
        "data": { "number": "102", "capacity": 80 }
      }
    ],
    "existing_reservations": [
      {
        "id": "E1",
        "data": {
          "title": "Aula Existente",
          "date": "2026-03-02",
          "start_time": "14:00",
          "end_time": "16:00"
        }
      }
    ],
    "subjects": [
      {
        "id": "DISC-001",
        "data": { "number_vacancies_offered": 60 }
      }
    ]
  }
  ```

- **Exemplo de Resposta (Sucesso):**
  ```json
  {
    "total_options": 2,
    "options": [
      {
        "place_id": "ROOM-101",
        "conflicts_to_move": []
      },
      {
        "place_id": "ROOM-202",
        "conflicts_to_move": ["RES-99"]
      }
    ]
  }
  ```

---

## 4. Execução com Docker

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
- `500 Internal Server Error`: Erro interno no processamento dos dados.