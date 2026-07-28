"""
Dike API — Web service para geração e alocação de reservas de salas.

Ponto de entrada principal da aplicação Flask.
"""

import logging
import sys
from flask import Flask, jsonify, request

from services.generate_service import GenerateService
from services.allocate_service import AllocateService

# Configure logging to write to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

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
        logger.warning("Received invalid or missing JSON for /api/generate")
        return jsonify({"error": "JSON inválido ou ausente."}), 400

    # Validar campos obrigatórios
    missing = []
    if 'subjects' not in data:
        missing.append('subjects')
    if 'places' not in data:
        missing.append('places')

    if missing:
        logger.warning(f"Missing required fields for /api/generate: {missing}")
        return jsonify({
            "error": "Campos obrigatórios ausentes.",
            "missing_fields": missing,
        }), 400

    num_subjects = len(data['subjects'])
    num_places = len(data['places'])
    logger.info(f"Starting generate request: {num_subjects} subjects, {num_places} places")

    try:
        result = GenerateService.generate(
            subjects=data['subjects'],
            places=data['places'],
            semester_start=data.get('semester_start'),
            semester_end=data.get('semester_end'),
        )
        stats = result.get('stats', {})
        logger.info(f"Generate success: assigned {stats.get('subjects_assigned')}/{stats.get('subjects_accepted')} accepted subjects. Success rate: {stats.get('success_rate')}%")
        return jsonify(result)

    except ValueError as exc:
        logger.error(f"Value error in generate: {exc}")
        return jsonify({"error": str(exc)}), 422

    except Exception as exc:
        logger.error(f"Internal error in generate: {exc}", exc_info=True)
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
        logger.warning("Received invalid or missing JSON for /api/allocate")
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
        logger.warning(f"Missing required fields for /api/allocate: {missing}")
        return jsonify({
            "error": "Campos obrigatórios ausentes.",
            "missing_fields": missing,
        }), 400

    new_res = data['new_reservation']
    num_places = len(data['places'])
    num_existing = len(data['existing_reservations'])
    logger.info(f"Starting allocate request for title '{new_res.get('title')}' (capacity needed: {new_res.get('capacity_needed') or new_res.get('capacity')}). {num_places} places, {num_existing} existing reservations.")

    try:
        result = AllocateService.allocate(
            new_reservation=data['new_reservation'],
            places=data['places'],
            existing_reservations=data['existing_reservations'],
            limit_moves=data.get('limit_moves', 3),
        )
        logger.info(f"Allocate success: found {result.get('total_options')} options.")
        return jsonify(result)

    except Exception as exc:
        logger.error(f"Internal error in allocate: {exc}", exc_info=True)
        return jsonify({
            "error": "Erro interno no processamento dos dados.",
            "detail": str(exc),
        }), 500


# ------------------------------------------------------------------ #
#  Error Handlers                                                     #
# ------------------------------------------------------------------ #

@app.errorhandler(404)
def not_found(_error):
    logger.warning(f"Endpoint not found: {request.url}")
    return jsonify({"error": "Endpoint não encontrado."}), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    logger.warning(f"Method not allowed: {request.method} on {request.url}")
    return jsonify({"error": "Método HTTP não permitido."}), 405


@app.errorhandler(500)
def internal_error(_error):
    logger.error("Internal Server Error handler triggered")
    return jsonify({
        "error": "Erro interno no processamento dos dados."
    }), 500


# ------------------------------------------------------------------ #
#  Entrypoint                                                         #
# ------------------------------------------------------------------ #

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3002, debug=False)
