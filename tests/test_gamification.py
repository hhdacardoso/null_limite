from datetime import datetime, timedelta

import main
from conftest import add_item, auth, make_user

ENERGY_BID = {
    "title": "Edital de energia",
    "roles": [{
        "name": "Gerente de Projeto",
        "criteria": [{"description": "Energia", "type": "sector_projects", "points_per_item": 2, "max_points": 8,
                      "sectors": ["energia"]}],
    }],
}


def project(name, sector="financeiro"):
    return main.Project(name=name, sector=sector, start_date=datetime(2020, 1, 1), end_date=datetime(2021, 1, 1))


def education():
    return main.Education(level=main.EducationLevel.MASTER, course="Engenharia")


def gamification(client, email):
    response = client.get("/professionals/me/gamification", headers=auth(client, email))
    assert response.status_code == 200
    return response.json()


def test_points_follow_evidence_status(client, db):
    pro = make_user(db, "pro@nexora.demo")
    add_item(db, pro, main.Certificate(name="PMP"), "approved")                                    # 12
    add_item(db, pro, main.Certificate(name="CSM", expiry_date=datetime(2020, 1, 1)), "approved")  # expired: 0
    add_item(db, pro, project("Pendente"), "pending")                                              # 5
    add_item(db, pro, project("Rejeitado"), "rejected")                                            # 0
    add_item(db, pro, project("Sem comprovante"))                                                  # 0
    add_item(db, pro, education(), "pending", with_file=False)                                     # 2

    data = gamification(client, "pro@nexora.demo")
    assert [i["status"] for i in data["items"]] == ["approved", "expired", "pending", "rejected", "missing", "pending_no_file"]
    assert data["items_points"] == 19
    assert data["freshness"] == "atualizado" and data["freshness_bonus"] == 20
    assert data["points"] == 39
    assert (data["level"], data["next_level"], data["points_to_next_level"]) == ("Bronze", "Prata", 1)


def test_freshness_tiers(client, db):
    now = main.utcnow()
    for email, days, expected in (("a@nexora.demo", 400, ("atencao", 10)), ("b@nexora.demo", 800, ("desatualizado", 0))):
        pro = make_user(db, email, created_at=now - timedelta(days=days))
        item = project("Antigo")
        item.created_at = now - timedelta(days=days)
        add_item(db, pro, item)
        data = gamification(client, email)
        assert (data["freshness"], data["freshness_bonus"]) == expected
        assert data["days_since_update"] == days
        update = next(m for m in data["missions"] if m["action"] == "update_profile")
        assert update["profile_points"] == 20 - expected[1]


def test_education_can_be_validated_without_file(client, db, manager):
    pro = make_user(db, "pro@nexora.demo")
    edu = add_item(db, pro, education())
    cert = add_item(db, pro, main.Certificate(name="PMP"))
    headers = auth(client, "pro@nexora.demo")

    no_file_cert = client.post("/professionals/me/evidences", data={"target_type": "certificate", "target_id": cert.id}, headers=headers)
    assert no_file_cert.status_code == 400
    response = client.post("/professionals/me/evidences", data={"target_type": "education", "target_id": edu.id}, headers=headers)
    assert response.status_code == 201 and response.json()["filename"] is None
    evidence_id = response.json()["id"]
    assert client.get(f"/evidences/{evidence_id}/file", headers=manager).status_code == 404
    assert gamification(client, "pro@nexora.demo")["items_points"] == 2

    overview = client.get("/gamification/overview", headers=manager).json()
    assert overview["pending_evidences"] == overview["pending_without_file"] == 1

    client.post(f"/evidences/{evidence_id}/review", json={"status": "approved"}, headers=manager)
    assert gamification(client, "pro@nexora.demo")["items_points"] == 12


def test_missions_rank_bid_impact_first(client, db, manager):
    pro = make_user(db, "pro@nexora.demo")
    add_item(db, pro, project("Banco"))
    energy = add_item(db, pro, project("Usina", sector="energia"))
    client.post("/bids", json=ENERGY_BID, headers=manager)

    missions = gamification(client, "pro@nexora.demo")["missions"]
    assert missions[0]["item_id"] == energy.id and missions[0]["profile_points"] == 12
    assert [(i["role_name"], i["gain"]) for i in missions[0]["bid_impact"]] == [("Gerente de Projeto", 2)]
    assert missions[1]["bid_impact"] == []

    headers = auth(client, "pro@nexora.demo")
    upload = client.post("/professionals/me/evidences", data={"target_type": "project", "target_id": energy.id},
                         files={"file": ("atestado.pdf", b"%PDF-1.4", "application/pdf")}, headers=headers)
    client.post(f"/evidences/{upload.json()['id']}/review", json={"status": "approved"}, headers=manager)
    data = gamification(client, "pro@nexora.demo")
    assert energy.id not in [m["item_id"] for m in data["missions"]]
    assert data["items_points"] == 12


def test_badges(client, db, manager):
    pro = make_user(db, "pro@nexora.demo")
    add_item(db, pro, main.Certificate(name="PMP"), "approved")
    for n in range(3):
        add_item(db, pro, project(f"Usina {n}", sector="energia"), "approved")
    other = make_user(db, "other@nexora.demo")
    add_item(db, other, project("Banco"), "pending")
    client.post("/bids", json=ENERGY_BID, headers=manager)

    codes = {b["code"] for b in gamification(client, "pro@nexora.demo")["badges"]}
    assert codes == {"perfil_comprovado", "em_dia", "certificado", "especialista_energia", "escalado"}
    assert {b["code"] for b in gamification(client, "other@nexora.demo")["badges"]} == {"em_dia"}


def test_rankings_respect_privacy(client, db, manager):
    make_user(db, "a@nexora.demo", team=main.Team.ENGINEERING)
    b = make_user(db, "b@nexora.demo", team=main.Team.ENGINEERING)
    add_item(db, b, project("Banco"), "approved")
    pro = auth(client, "a@nexora.demo")

    assert client.get("/leaderboard/professionals", headers=pro).status_code == 403
    assert client.get("/gamification/overview", headers=pro).status_code == 403
    teams = client.get("/leaderboard/teams", headers=pro).json()
    assert teams == [{"team": "engenharia", "members": 2, "avg_points": 26.0, "updated_pct": 100.0, "proven_items_pct": 100.0}]

    me = gamification(client, "a@nexora.demo")
    assert (me["position"], me["team_position"], me["team_size"]) == (2, 2, 2)
    board = client.get("/leaderboard/professionals", headers=manager).json()
    assert [e["full_name"] for e in board] == ["b", "a"]  # managers are not ranked
