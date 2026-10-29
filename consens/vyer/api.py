"""JSON-API. v0.1: läsendpoints för signaler och avtal."""
from flask import Blueprint, jsonify

from ..db import get_db

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/signaler")
def signaler():
    rader = get_db().execute(
        "SELECT s.id, s.typ, s.titel, s.varde, s.varde_sort, s.cpv, s.lan, "
        "s.kalla, s.extern_id, s.deadline, o.namn AS organisation, o.orgnr "
        "FROM signal s JOIN organisation o ON o.id=s.organisation_id "
        "ORDER BY s.id DESC LIMIT 500"
    ).fetchall()
    return jsonify([dict(r) for r in rader])


@bp.route("/avtal")
def avtal():
    rader = get_db().execute(
        "SELECT a.id, a.titel, a.leverantor, a.varde, a.varde_sort, "
        "a.antal_anbud, a.slutdatum, a.cpv, a.kalla, a.extern_id, "
        "o.namn AS organisation, o.orgnr "
        "FROM avtal a JOIN organisation o ON o.id=a.organisation_id "
        "ORDER BY a.slutdatum LIMIT 500"
    ).fetchall()
    return jsonify([dict(r) for r in rader])
