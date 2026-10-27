import itertools
import random
from datetime import datetime

import main
from conftest import auth, make_user

PM_BID = {
    "title": "Edital de teste",
    "roles": [{
        "name": "Gerente de Projeto",
        "criteria": [
            {"description": "PMP", "type": "certification", "max_points": 10, "keywords": ["pmp"]},
            {"description": "Energia", "type": "sector_projects", "points_per_item": 2, "max_points": 8, "sectors": ["energia"]},
            {"description": "Pós", "type": "education", "max_points": 5, "min_level": "specialization"},
            {"description": "Anos", "type": "years_experience", "max_points": 5, "min_years": 4},
        ],
    }],
}


def add_item(db, owner, item, status=None):
    """Add a profile item and, unless status is None, one evidence with that status."""
    item.professional_id = owner.id
    db.add(item)
    db.flush()
    if status:
        target = {main.Certificate: "certificate_id", main.Project: "project_id", main.Education: "education_id"}[type(item)]
        db.add(main.Evidence(professional_id=owner.id, filename="x.pdf", file_path="/nonexistent", file_size=1,
                             status=main.EvidenceStatus(status), **{target: item.id}))
    db.commit()
    return item


def energy_project(year):
    return main.Project(name=f"P{year}", sector="energia", start_date=datetime(year, 1, 1), end_date=datetime(year + 1, 1, 1))


def scores_by_criterion(candidate):
    return {c["description"]: (c["points"], c["potential_points"]) for c in candidate["criteria"]}


def test_only_managers_access_bids(client, db, manager):
    make_user(db, "pro@nexora.demo")
    pro = auth(client, "pro@nexora.demo")
    assert client.post("/bids", json=PM_BID, headers=pro).status_code == 403
    assert client.get("/bids", headers=pro).status_code == 403
    bid_id = client.post("/bids", json=PM_BID, headers=manager).json()["id"]
    assert client.get(f"/bids/{bid_id}/team", headers=pro).status_code == 403
    assert client.get(f"/bids/{bid_id}", headers=manager).json()["max_points"] == 28


def test_invalid_bids_are_rejected(client, manager):
    def with_criterion(**criterion):
        return {"title": "x", "roles": [{"name": "r", "criteria": [{"description": "c", "max_points": 1, **criterion}]}]}

    assert client.post("/bids", json=with_criterion(type="certification"), headers=manager).status_code == 422
    assert client.post("/bids", json=with_criterion(type="sector_projects", points_per_item=1, sectors=["petroleo"]),
                       headers=manager).status_code == 422
    too_big = {"title": "x", "roles": [{"name": "r", "quantity": 9, "criteria": PM_BID["roles"][0]["criteria"]}]}
    assert client.post("/bids", json=too_big, headers=manager).status_code == 422


def test_only_approved_items_score(client, db, manager):
    pro = make_user(db, "pro@nexora.demo", years_experience=20)
    add_item(db, pro, main.Certificate(name="CAPM"), "approved")  # must not match "pmp"
    pmp = add_item(db, pro, main.Certificate(name="Project Management Professional (PMP)"), "pending")
    for year in range(2015, 2020):
        add_item(db, pro, energy_project(year), "approved")
    add_item(db, pro, energy_project(2020), "rejected")
    add_item(db, pro, main.Project(name="Banco", sector="financeiro"), "approved")
    add_item(db, pro, main.Education(level=main.EducationLevel.MASTER, course="Engenharia"))  # no evidence

    bid_id = client.post("/bids", json=PM_BID, headers=manager).json()["id"]
    candidate = client.get(f"/bids/{bid_id}/ranking", headers=manager).json()[0]["candidates"][0]
    assert scores_by_criterion(candidate) == {
        "PMP": (0, 10),     # pending: potential only
        "Energia": (8, 8),  # 5 approved x 2 capped at 8, rejected ignored
        "Pós": (0, 0),      # no evidence uploaded
        "Anos": (5, 5),     # 5 years proven by approved projects
    }

    evidence_id = db.query(main.Evidence).filter_by(certificate_id=pmp.id).one().id
    team = client.get(f"/bids/{bid_id}/team", headers=manager).json()
    assert team["points"] == 13 and team["potential_points"] == 23
    assert team["pending_gains"][0]["evidence_id"] == evidence_id and team["pending_gains"][0]["gain"] == 10

    review = client.post(f"/evidences/{evidence_id}/review", json={"status": "approved"}, headers=manager)
    assert review.status_code == 200
    assert client.get(f"/bids/{bid_id}/team", headers=manager).json()["points"] == 23


def test_expired_certificate_and_declared_years_do_not_score(client, db, manager):
    pro = make_user(db, "pro@nexora.demo", years_experience=30)
    add_item(db, pro, main.Certificate(name="PMP", expiry_date=datetime(2020, 1, 1)), "approved")
    add_item(db, pro, energy_project(2010), "approved")

    bid_id = client.post("/bids", json=PM_BID, headers=manager).json()["id"]
    candidate = client.get(f"/bids/{bid_id}/ranking", headers=manager).json()[0]["candidates"][0]
    scores = scores_by_criterion(candidate)
    assert scores["PMP"] == (0, 0)
    assert candidate["criteria"][0]["items"][0]["status"] == "expired"
    assert scores["Anos"] == (0, 5)  # 1 proven year; the 30 declared years are potential only


def test_team_never_repeats_a_professional(client, db, manager):
    star = make_user(db, "star@nexora.demo")
    other = make_user(db, "other@nexora.demo")
    add_item(db, star, main.Certificate(name="PMP"), "approved")
    add_item(db, star, main.Certificate(name="CSM"), "approved")
    add_item(db, other, main.Certificate(name="CSM"), "approved")
    bid = {"title": "x", "roles": [
        {"name": "GP", "criteria": [{"description": "PMP", "type": "certification", "max_points": 10, "keywords": ["pmp"]}]},
        {"name": "SM", "criteria": [{"description": "CSM", "type": "certification", "max_points": 5, "keywords": ["csm"]}]},
    ]}
    bid_id = client.post("/bids", json=bid, headers=manager).json()["id"]
    team = client.get(f"/bids/{bid_id}/team", headers=manager).json()
    assert [s["member"]["full_name"] for s in team["slots"]] == ["star", "other"]
    assert team["points"] == 15


def candidate(pid, points, potential=0):
    return main.CandidateScore(professional_id=pid, full_name=str(pid), points=points,
                               potential_points=max(points, potential), max_points=100, criteria=[])


def test_best_team_matches_brute_force():
    rng = random.Random(1)
    for _ in range(300):
        people, n_slots = rng.randint(1, 7), rng.randint(1, 4)
        slots = [sorted((candidate(p, rng.randint(0, 10), rng.randint(0, 12)) for p in range(people)),
                        key=lambda c: (c.points, c.potential_points), reverse=True) for _ in range(n_slots)]

        team = main.best_team([options[:n_slots] for options in slots])
        got = (sum(c.points for c in team if c), sum(c.potential_points for c in team if c))
        filled = [c.professional_id for c in team if c]
        assert len(filled) == len(set(filled)) == min(people, n_slots)

        best = (-1, -1)
        for chosen in itertools.permutations(range(people), min(people, n_slots)):
            for picked_slots in itertools.combinations(range(n_slots), len(chosen)):
                members = [slots[s][next(i for i, c in enumerate(slots[s]) if c.professional_id == p)]
                           for s, p in zip(picked_slots, chosen)]
                best = max(best, (sum(c.points for c in members), sum(c.potential_points for c in members)))
        assert got == best
