# Energy model

**Code:** [`calculate_energy_consumption()`](../route_optimizer_backend.py#L197) in `route_optimizer_backend.py`.
It is called once per route leg (one stop to the next) from `optimize_route()`:

- single-vehicle routes: [delivery legs](../route_optimizer_backend.py#L832), [return leg](../route_optimizer_backend.py#L888)
- fleet routes (OR-Tools): [all legs](../route_optimizer_backend.py#L1030)

The model is called **Model E**. It is a polynomial regression for energy per kilometre of an
Isuzu N-Series EV. The fit statistics quoted in the code are R² = 0.9997, MAPE = 3.46% and test
RMSE = 0.38 kWh, with a target range of 0.85–1.3 kWh/mile. Model E extends an earlier "Model D"
with two air-density interaction terms (v²·T and v²·altitude). Aerodynamic drag scales with
ρ·v², and air density ρ falls as temperature and altitude rise.

> This repository holds only the fitted coefficients. The training data and the fitting
> notebook are not here.

---

## Formula

For one leg:

```
g        = Δh / d                                       road grade, metres per km

e_per_km = β0 + β1·v + β2·T + β3·W + β4·Δh + β5·v²
         + β6·(W·g) + β7·(v·g) + β8·(v²·T) + β9·(v²·h)
         + HVAC                                          0.092 if climate control is on, else 0

E_leg    = e_per_km · d                                  kWh
```

A route's energy is the sum of `E_leg` over its legs. That sum includes the return to the
depot, because the UI always sends closed routes.

### Inputs

| Symbol | Meaning | Unit | Where the value comes from |
|---|---|---|---|
| d | Driving distance of the leg | km | Google Distance Matrix ([`get_distance_matrix`](../route_optimizer_backend.py#L71)), metres ÷ 1000 |
| v | Average speed over the leg | km/h | d ÷ Google's typical driving duration ([`estimate_average_speed`](../route_optimizer_backend.py#L308)). No live traffic |
| T | Ambient temperature | °C | UI slider, `energy_params.temperature_c` (default 20). Same value for every leg |
| W | Gross vehicle weight on this leg | tonnes | Vehicle `weight_lbs` ÷ 2204.62, reduced as cargo is delivered (see [Weight model](#weight-model)) |
| Δh | Elevation change, end minus start (+ = uphill) | m | Google Elevation API at the two stop coordinates ([`get_elevation_data`](../route_optimizer_backend.py#L165)) |
| h | Elevation at the start of the leg | m above sea level | Same call |
| HVAC | Climate-control overhead | kWh/km | 0.092 when `has_climate_control` is true. Added on top of the regression, not fitted |

### Coefficients

Units: distance in km, speed in km/h, temperature in °C, weight in tonnes, elevation in m, grade in m/km.

| Coefficient | Term | Value | Physical reading |
|---|---|---|---|
| β0 | intercept | 5.7142142481e-01 | Base consumption |
| β1 | v | −1.0081493644e-02 | Linear speed term. Together with β5 it puts the lowest consumption at about 41 km/h (at 20 °C, 200 m) |
| β2 | T | 1.4087882356e-04 | Temperature |
| β3 | W | 1.8176062830e-02 | Rolling resistance and mass |
| β4 | Δh | 1.7585256876e-05 | Elevation change |
| β5 | v² | 1.2923132040e-04 | Aerodynamic drag |
| β6 | W·g | 2.8886974460e-03 | Climbing a grade while heavy |
| β7 | v·g | −9.9890433238e-06 | Speed–grade interaction |
| β8 | v²·T | −2.6120414024e-07 | Drag falls as air warms (lower density) |
| β9 | v²·h | −8.6411314209e-09 | Drag falls with altitude (lower density) |

### Per-term output

Every leg in the `/api/optimize` response includes `energy_breakdown`. It gives each term's
contribution in kWh for that leg, and the terms add up to the leg's `energy_kwh`:

```
intercept_kwh, speed_kwh, temperature_kwh, weight_kwh, altitude_diff_kwh, speed_sq_kwh,
weight_grade_kwh, speed_grade_kwh, aero_temp_kwh, aero_alt_kwh, climate_control_kwh
```

The function also returns `energy_per_km`, `energy_per_mile`, `kwh_per_mile` and `miles_per_kwh`.

### Worked example

A 10 km flat leg at 48 km/h and 20 °C, with a 13,000 lbs (5.90 t) truck starting at 200 m elevation and HVAC on:

| Term | kWh |
|---|---|
| intercept | 5.714 |
| speed (β1·v) | −4.839 |
| speed² (β5·v²) | 2.978 |
| weight (β3·W) | 1.072 |
| HVAC | 0.920 |
| aero × temperature | −0.120 |
| aero × altitude | −0.040 |
| temperature | 0.028 |
| grade terms, Δh | 0 (flat) |
| **Total** | **5.71 kWh = 0.571 kWh/km = 0.92 kWh/mile** |

How the output moves with each input. These are model outputs on a 10 km flat leg at 200 m with
HVAC on, not measurements:

| Vary | Values → kWh/mile |
|---|---|
| Speed (13,000 lbs, 20 °C) | 25 km/h → 0.96 · 40 → 0.91 · 60 → 0.98 · 90 → 1.38 · 105 → 1.71 |
| Temperature (60 km/h, 13,000 lbs) | −10 °C → 1.02 · 20 °C → 0.98 · 40 °C → 0.95 |
| Weight (60 km/h, 20 °C) | 10,000 lbs → 0.94 · 19,500 lbs → 1.07 |

---

## Weight model

**Code:** [vehicle config](../route_optimizer_backend.py#L740) and [leg weights](../route_optimizer_backend.py#L832) for single-vehicle routes; [per-vehicle config](../route_optimizer_backend.py#L1017) for fleet routes.

- Each vehicle's `weight_lbs` is its **gross weight leaving the depot**: truck, batteries and cargo
  together. The form's range is 10,000–19,500 lbs.
- The **empty-truck weight** depends on the pack count:
  `7,000 lbs + (packs − 4) × 440.9 lbs`, since one pack is 200 kg (440.9 lbs). This is
  `vehicle_base_weight_lbs()` inside `optimize_route()`.
- **Cargo** = max(0, gross − empty), split evenly across the route's delivery stops.
- Leg *i* (i = 0 is the leg leaving the depot) is driven at `gross − i × cargo_per_stop`.
- The **return leg** to the depot is driven at the empty-truck weight.
- In the UI, changing the pack count also resets the weight field to
  `13,000 − 4 × 440.9 + packs × 440.9` lbs. That is 13,000 lbs at 4 packs, plus 440.9 lbs for each
  extra pack ([`updateVehicle`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L65)).
  You can still edit the weight by hand afterwards.

## Battery

- Capacity = packs × 20 kWh. The UI allows 3–9 packs, so 60–180 kWh. The default is 4 packs = 80 kWh.
- **Fleet routes only** (more than one vehicle): each route reports `battery_remaining_kwh`
  (capacity − route energy, floored at 0), `battery_remaining_pct`, and `battery_depleted`
  (true when energy > capacity). The UI shows the bar orange below 20% and red when depleted.
  [Code](../route_optimizer_backend.py#L1080).
- Single-vehicle results report total energy but **do not check it against the battery**.
- The model has no charging stops, reserve margin or state-of-charge limits.

## Other inputs and what they affect

| Input | Default | Affects |
|---|---|---|
| `temperature_c` | 20 °C | Energy (T terms) |
| `has_climate_control` | on | Energy (+0.092 kWh/km) |
| `unload_time_min` | 30 min per stop | Route **time** only. Energy is not affected, and HVAC while parked is not counted |
| `energy_params.total_weight_lbs` | 19,500 | **Unused.** Weight always comes from the `vehicles` array, or 13,000 lbs when none is sent |
| `energy_params.base_weight_lbs` | 7,000 | Only the *displayed* weight on the single-vehicle return leg. The energy on that leg uses the pack-based empty weight |

---

## How energy relates to the solvers

**The solvers never see energy.** They minimize the distance matrix (`mode: "distance"`,
which the UI always sends) or the time matrix (`mode: "time"`). Energy is then calculated leg
by leg on whatever route the solver returned. "Optimal" here means shortest distance, which is
not necessarily lowest energy. For example, a route that climbs early while fully loaded can be
short but costly.

To make routing energy-aware, the hook is the `matrix` that [`optimize_route()`](../route_optimizer_backend.py#L725)
passes to the solvers. You could build an energy matrix (one `calculate_energy_consumption`
call per ordered pair of stops) and pass that instead. Two things to handle: weight depends on
where a leg falls in the route, so a fixed matrix can only approximate it; and OR-Tools needs
integer costs (for example Wh).

## Caveats in the model as coded

- **Δh enters per km.** `β4·Δh` is added to the per-km value and then multiplied by distance,
  so its total effect grows with leg length (β4·Δh·d). The grade terms (β6, β7) already carry the
  per-km slope. This is how the model is specified; check it if that was not intended.
- **Elevation is sampled only at the stops**, not along the road. Hills between two stops at
  similar heights cancel out.
- **Energy can be negative on steep downhill legs.** For example, −200 m over 5 km at 19,500 lbs
  gives −1.9 kWh. Nothing clamps it, so it behaves like unlimited regenerative braking.
- **If the Elevation API call fails, every elevation silently becomes 0** (a flat world).
- **Speed is an average** (distance ÷ typical duration), so stop-and-go driving is not modelled.
- **Which vehicle the data came from:** the UI header says "Mercedes eActros Energy Model", while
  the code says Model E for the Isuzu N-Series EV. Worth confirming with whoever fitted it.
