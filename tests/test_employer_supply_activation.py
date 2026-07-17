from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _endpoint_key(adapter: str, endpoint: object) -> tuple[str, str]:
    if adapter == "workday":
        assert isinstance(endpoint, dict)
        value = "|".join(
            str(endpoint[field]).lower() for field in ("tenant", "host", "site")
        )
    else:
        value = str(endpoint).lower()
    return adapter, value


def test_supply_activation_has_four_active_rotating_25_employer_groups() -> None:
    activation = yaml.safe_load(
        (PROJECT_ROOT / "config" / "employer_supply_activation.v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    companies = yaml.safe_load(
        (PROJECT_ROOT / "config" / "companies.yaml").read_text(encoding="utf-8")
    )

    waves = activation["waves"]
    assert activation["selected_count"] == 100
    assert len(waves) == 4
    assert [len(wave["employers"]) for wave in waves] == [25, 25, 25, 25]
    assert [wave["status"] for wave in waves] == ["active_rotating"] * 4

    selected = [
        _endpoint_key(employer["adapter"], employer["endpoint"])
        for wave in waves
        for employer in wave["employers"]
    ]
    assert len(selected) == len(set(selected)) == 100

    configured = {
        _endpoint_key(adapter, endpoint)
        for adapter in ("greenhouse", "lever", "ashby", "workday")
        for endpoint in companies.get(adapter, [])
    }
    assert set(selected) <= configured


def test_rotation_selects_one_group_and_defers_the_other_75() -> None:
    from src.jobs.supply_activation import load_supply_refresh_rotation

    rotations = [load_supply_refresh_rotation(cycle) for cycle in range(4)]
    assert [rotation.group_id for rotation in rotations] == [
        "wave-1",
        "wave-2",
        "wave-3",
        "wave-4",
    ]
    assert all(rotation.approved_endpoint_count == 100 for rotation in rotations)
    assert all(len(rotation.live_endpoint_keys) == 25 for rotation in rotations)
    assert all(len(rotation.deferred_endpoint_keys) == 75 for rotation in rotations)
    assert len(set().union(*(set(value.live_endpoint_keys) for value in rotations))) == 100
