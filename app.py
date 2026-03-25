"""
Dike API — Web service para geração e alocação de reservas de salas.

Ponto de entrada principal da aplicação Flask.
"""

from flask import Flask, jsonify, request

from services.generate_service import GenerateService
from services.allocate_service import AllocateService

app = Flask(__name__)


# ------------------------------------------------------------------ #
#  Health Check                                                       #
# ------------------------------------------------------------------ #

@app.route('/api/health', methods=['GET'])
def health():
    """Verifica se o serviço está operando corretamente."""
    return jsonify({"status": "ok"})


# ------------------------------------------------------------------ #
#  Gerar Reservas                                                     #
# ------------------------------------------------------------------ #

@app.route('/api/generate', methods=['POST'])
def generate():
    """Gera grade completa de reservas para um semestre."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON inválido ou ausente."}), 400

    # Validar campos obrigatórios
    missing = []
    if 'subjects' not in data:
        missing.append('subjects')
    if 'places' not in data:
        missing.append('places')

    if missing:
        return jsonify({
            "error": "Campos obrigatórios ausentes.",
            "missing_fields": missing,
        }), 400

    try:
        result = GenerateService.generate(
            subjects=data['subjects'],
            places=data['places'],
            semester_start=data.get('semester_start'),
            semester_end=data.get('semester_end'),
        )
        return jsonify(result)

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422

    except Exception as exc:
        return jsonify({
            "error": "Erro interno no processamento dos dados.",
            "detail": str(exc),
        }), 500


# ------------------------------------------------------------------ #
#  Alocar / Sugestão de Vaga                                          #
# ------------------------------------------------------------------ #

@app.route('/api/allocate', methods=['POST'])
def allocate():
    """Busca opções de alocação para uma nova reserva."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON inválido ou ausente."}), 400

    # Validar campos obrigatórios
    missing = []
    if 'new_reservation' not in data:
        missing.append('new_reservation')
    if 'places' not in data:
        missing.append('places')
    if 'existing_reservations' not in data:
        missing.append('existing_reservations')

    if missing:
        return jsonify({
            "error": "Campos obrigatórios ausentes.",
            "missing_fields": missing,
        }), 400

    try:
        result = AllocateService.allocate(
            new_reservation=data['new_reservation'],
            places=data['places'],
            existing_reservations=data['existing_reservations'],
            limit_moves=data.get('limit_moves', 3),
            subjects=data.get('subjects', []),
        )
        return jsonify(result)

    except Exception as exc:
        return jsonify({
            "error": "Erro interno no processamento dos dados.",
            "detail": str(exc),
        }), 500


# ------------------------------------------------------------------ #
#  Error Handlers                                                     #
# ------------------------------------------------------------------ #

@app.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "Endpoint não encontrado."}), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    return jsonify({"error": "Método HTTP não permitido."}), 405


@app.errorhandler(500)
def internal_error(_error):
    return jsonify({
        "error": "Erro interno no processamento dos dados."
    }), 500


# ------------------------------------------------------------------ #
#  Entrypoint                                                         #
# ------------------------------------------------------------------ #

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3002, debug=False)
