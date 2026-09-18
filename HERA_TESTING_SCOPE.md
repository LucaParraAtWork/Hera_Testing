# Hera — Application & Testing Scope

This documents what Hera actually does and why, built from two sources: everything learned
automating and reviewing the UAT test plan in `UAT Testing/` over the past weeks, plus a
read-through of `hera-virtual-plant/` (the MQTT simulator that stands in for the physical
plant during demos/UAT). It is a first draft for the team to correct, not a spec — several
items below are flagged as open questions rather than settled facts.

## 1. What Hera is

Hera schedules and settles hydrogen truck loading/unloading against a physical electrolyzer
plant (H₂ from Elia/electricity via electrolysis) and reconciles the plan against what
actually happened. Two offtakers share the plant today: **Virya Energy NV** and **Messer
Belgium NV**. The lifecycle, in order:

1. **Nominate** — an offtaker submits (CSV import or manual UI) which trailers it wants
   dropped off / picked up, and when, for a given week or year.
2. **Schedule** — nominations become slots on a weekly grid; a slot can be confirmed or
   rejected. Confirming turns a nomination into a real commitment the plant will execute.
3. **Execute at the plant** — trucks physically arrive, drop off empty trailers, the plant
   fills them, trucks pick full trailers back up. This is the part `hera-virtual-plant`
   simulates (see §2).
4. **Monitor & cost** — Hera compares its own forecast (Profiles) against what the plant
   telemetry actually reports (Plant Monitoring/Communication), and turns the whole week's
   physical execution into a cost figure per offtaker (NCC / Costs Breakdown).
5. **Maintain** — planned maintenance windows on individual assets (electrolyser stack,
   compressor, a specific loading bay) constrain what the plant is allowed to do during
   that window, and Hera must reflect that in profiles/occupancy.

## 2. The Hera ↔ Plant contract

`hera-virtual-plant` (React + FastAPI, with a legacy Streamlit `app.py` still used for manual
testing) is **not the real plant** — it's a simulator a tester runs locally so Hera has
something to talk to over MQTT without real hardware. Understanding its contract explains
*why* several Hera screens exist and what they're really checking.

### MQTT contract

| Direction | Topic pattern | Meaning |
|---|---|---|
| Hera → Plant (CMD) | `HOF/PLC/ELY/CMD/power_sp` | Electrolyser power setpoint (MW) |
| Hera → Plant (CMD) | `HOF/PLC/DI<n>/CMD/fill_rate_profile` | Per-bay dispenser fill-rate profile |
| Hera → Plant (CMD) | `HOF/ACS/ACS/CMD/db_schedules` | Truck drop-off/pick-up slot schedule |
| Plant → Hera (TLM) | `HOF/PLC/OKS/TLM/flow_rate_mass/0.8.3` | Gas-network injection (kg/h) |
| Plant → Hera (TLM) | `HOF/PLC/DI<n>/TLM/fill_rate_meas/0.8.3` | Actual per-bay fill rate (g/s) |
| Plant → Hera (EVT) | `HOF/ACS/ACS/EVT/activity/0.8.3` | Truck movement / bay state events |

### Truck lifecycle state machine (per loading bay, `operating_mode` 0–12)

This is the real sequence a bay goes through for one trailer, confirmed from
`hera-virtual-plant/app.py`'s `_OPERATING_MODE_LABELS` and scenario builder — it's the
ground truth behind every Drop-Off/Pick-Up/Bay-Occupancy test in the UAT plan:

`0 Reset/Pickup closed → 1 Drop-off start → 2 Vehicle approach → 3 Drop-off closed →
4 Ready → 5 Authorized → 6 Filling → 8 Fill done → 9 Wait disconnect → 10 Wait pickup →
11 Pickup created → 12 Vehicle leaving → back to 0`

A truck **entry/exit** is a separate ACS event pair (`mvt_requested`/`mvt_completed`,
gate `ENT`/`EXT`) correlated by a `correlation_id`, not tied 1:1 to a single bay's mode
sequence — a truck can enter, drop off at one bay, and later a *different* truck's exit
picks up from that bay once it's full. Bay Occupancy's three phases (Drop / Filling / Pick)
are derived from this same sequence, which is exactly what
`Scheduled Transfers/test_scheduled_transfers_profiles.py`'s alignment checks
(`SCHED_TRF_04`–`06`) already verify from the API side.

**A concrete, documented edge case worth its own test** (from
`hera-virtual-plant/hera_mqtt_test_scenario.txt`, "Rule K"): if a bay's mode-11
(*Pickup created*) event fires **before** the next truck's own entry+drop-off events are
fully processed, the backend can fail to link that truck's Exit to a TransferId, leaving it
`NULL`/unlinked. This is a real, previously-hit timing bug in the reference scenario, not a
hypothetical — worth a dedicated negative test once we can control event ordering that
precisely.

### Physics constants — **discrepancy to verify with the team**

| Constant | `hera-virtual-plant` (simulator) | `parameters.csv` (our NCC_CALC_07 reference) |
|---|---|---|
| Min stable operating level | MSOL = **3.8 MW** | M_SOL = **5.0 MW** |
| Max continuous rating | MCR = **23.3 MW** | P_MAX = **25.0 MW** |
| Electrolysis efficiency | **70%** | ETA = **0.643** (64.3%) |

These three numbers disagree between the simulator we test against and the parameters our
own Python NCC reference calculation (`NetCommodityCost/test_ncc_costs_breakdown.py`) uses.
I don't know which side is stale or whether they're intentionally different (e.g. the real
plant's spec vs. the simulator's simplified approximation) — flagging rather than guessing.
If `parameters.csv` should match the simulator, NCC_CALC_07's real API-vs-Python mismatches
we've already seen this session may be partly explained by this, on top of the
50/50-fallback and stale-scheduling issues already fixed.

One thing that does line up: `PI_AFRR = 0` in `parameters.csv`, and the virtual plant's own
NCC reproduction (`app.py::render_ncc_tab`) has no aFRR term at all — so that part of our
formula currently contributing exactly zero isn't a live discrepancy, just something to
re-check the day aFRR pricing becomes non-zero.

### NCC formula, confirmed from the simulator's own reproduction

```
TruckFilling / VoluntaryInjection (Hydrogen, g/s):
    elec_MWh = Σ flow_to_power(flow_gs, U-curve) × 0.25h
    kg       = Σ flow_gs × 900 / 1000

MsolCoveragePenalty (Electricity kW + Hydrogen g/s):
    msol_elec_MWh = Σ (kW / 1000) × 0.25h
    msol_kg       = Σ flow_gs × 900 / 1000

Net cost = (TruckFilling elec × price_elec)
         + (VoluntaryInjection elec × price_elec) − (VoluntaryInjection kg-equiv × price_gas)
         + (MsolCoveragePenalty elec × price_elec) − (MsolCoveragePenalty kg-equiv × price_gas)

NCC [€/kg] = Net cost / (TruckFilling kg + MsolCoveragePenalty kg)
```

The flow→power conversion uses a piecewise U-curve (3 reference points: MSOL/BEP/MCR, each
a flow-in-g/s → specific-consumption-in-kWh/Nm³ pair) rather than a flat efficiency — worth
checking whether `test_ncc_costs_breakdown.py`'s Python reference uses the same curve or a
flat `ETA`, since that's a second possible source of the NCC_CALC_07 mismatches.

## 3. Testing scope by area

| Area | What it really tests | Automated? |
|---|---|---|
| **Nominations** (Weekly/Yearly) | CSV import validation, slot confirm/reject, drop-off/pick-up sequencing rules against the real truck state machine (§2) | `Weekly Nomination/`, `Yearly Nomination/` — largely automated, several negative-CSV business rules still open questions for the product owner (see memory: Nom_WN_NEG_05/08/15/17) |
| **Scheduler / Bay Occupancy** | Confirm/Reject (individual + bulk), Drop/Filling/Pick occupancy windows derived from the state machine | `Weekly Nomination/test_weekly_ui.py` (UI_06–11), `Scheduled Transfers/` |
| **Profiles** | Truck Filling (g/s) / Gas injection / Power consumption per 15-min step vs. maintenance caps | `Scheduled Transfers/`, `EndToEndPlannedMaintenance/` |
| **NCC / Costs Breakdown** | Per-offtaker cost formula (§2), UI navigation, unit toggle | `NetCommodityCost/test_ncc_costs_breakdown.py` — needs the parameter/curve check above before its Python-vs-API comparisons can be fully trusted |
| **Master Data / ACS / Truck Manager** | Trucks/drivers/trailers/companies CRUD, Qualification state machine (Pre-Qualified → Qualified requires dates) | `Master Data/test_master_data.py` |
| **Planned Maintenance** | Maintenance windows on an asset must clamp Profiles for that window (0 MW for a stack, 12.5 MW for HPC half-power, etc.) | `Planned Maintenance/`, `EndToEndPlannedMaintenance/` |
| **Plant Monitoring / Communication** | MQTT CMD/TLM contract itself, live dashboards | **No automation** — this is the one area only `hera-virtual-plant` (manual Streamlit tool) currently exercises; a real gap |
| **Bidding, Invoicing, Plant capacity/connection** | Not yet investigated this session | No automation, no notes yet |

## 4. Open questions to raise with the team

1. **Physics constants** (§2) — should `parameters.csv` (M_SOL/P_MAX/ETA) match the
   simulator's MSOL/MCR/70%, or are they legitimately different specs?
2. **NCC efficiency model** — flat `ETA` vs. the simulator's piecewise U-curve: does the
   real Hera backend use the curve too?
3. **"Rule K" timing edge case** (§2) — is this still reproducible on the current Hera
   version, and is it worth a dedicated negative test once we can sequence MQTT events
   precisely enough to trigger it on demand?
4. **Plant Monitoring / Communication** has zero automated coverage — worth scoping given
   `hera-virtual-plant`'s Truck Scenario tab already gives us a scriptable way to generate
   the MQTT events a test would need.

## 5. Where this connects to existing automation

Everything above maps onto the 10 Playwright scripts and `run_all_tests.py` in
`UAT Testing/`, and the layered Excel build scripts documented in this session's own
memory notes (`project_hera_uat_plan`). This file is meant to sit alongside that as the
*why*, not duplicate the *how* — see `UAT Testing/run_all_tests.py`'s own docstring and the
Excel's "Covered by Scripts" / "Missing from Scripts" sheets for current coverage status.
