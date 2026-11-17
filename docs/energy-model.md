# City NN energy model

The planner, truck limits, charging and incident detours, benchmark, and battery-drain simulation use one frozen neural network: `energy_nn_results_city.json`. There is no runtime model selector or fallback energy predictor. Missing or malformed weights return `503 model_unavailable`; an unsupported `energy_params.model` returns `400`.

## Inference

`energy_model/inference.py` loads the frozen `energy_nn_v1` ensemble. Each member has 12 normalized inputs, a 32-unit ReLU layer, and one linear output. Members are averaged, then propulsion energy is floored at zero. The artifact is validated for input order, shapes and finite parameters. A request pins the loaded weights for its entire calculation.

The ten physical inputs represent distance, average speed, temperature, load, aerodynamic speed squared, temperature and altitude interactions, weighted ascent/descent, and positive acceleration work. Two indicator inputs distinguish Eco and Normal; both are zero for Aggressive.

Google supplies distance, duration and endpoint elevations, not second-by-second telemetry. `aggregate.py` constructs deterministic synthetic acceleration, cruise, coast and braking traces using seed 42, rescaling to match the supplied leg distance and duration. Elevation varies along travelled distance between endpoints. `features.py` and `traces.py` compute the NN inputs. Cached features include style, distance, duration, endpoint elevations and temperature; weight-dependent features are scaled linearly. The cache does not reuse another style's features.

Total predicted energy is:

```
max(0, ensemble propulsion prediction)
  + driving_hours * (5 kW base auxiliary + 5 kW ePTO + HVAC)
```

HVAC contributes 4 kW when enabled and zero when disabled. The response breakdown is `propulsion`, `base_aux`, `epto` and `hvac`, all in kWh. Unloading adds service time but no driving energy in this demo.

## Driver defaults and run overrides

Saved driver profiles contain a stable `id`, `name` and usual `driving_style`: `eco`, `normal` or `aggressive`. A vehicle's effective style is its explicit `driving_style_override`, otherwise the assigned driver's snapshotted usual style, otherwise Normal. A run override never updates driver memory. Moving a driver clears the old binding and clears overrides on the changed assignments. Battery packs, capacity and vehicle weight stay attached to the truck.

Style controls synthetic acceleration, braking, coasting and speed variation. For legs averaging above 50 km/h, cruise-time assumptions also scale duration by `1 / 0.9` for Eco or `1 / 1.1` for Aggressive. Normal uses the supplied duration. These are simulation assumptions. There is no fixed percentage energy saving or penalty: the NN evaluates each resulting trace. Raw traffic matrices are retained in responses and reapplied once for each vehicle.

## How energy relates to the solvers

Every truck gets its own objective cost, time and energy-bound matrices. Energy objectives evaluate each truck at its midpoint load; objective normalization uses common fleet scales so more efficient trucks keep their relative advantage. Routes are evaluated again after solving, with cargo unloading after each delivery, using the same style and weights. Different profiles can change stop assignment, order, travel time and battery feasibility; they need not produce a different route on every input.

Fleet battery constraints use a maximum over the feasible load interval for each arc. The frozen network is piecewise affine in load. Evaluating both load endpoints and every first-layer activation boundary across all ensemble members gives its maximum, including an interior peak. Bounds round up to Wh. A normal completed return uses empty weight. Breakdown rescue and routes that may carry undelivered cargo use an interval for the return too. The final fleet route receives a second battery/reserve check.

This bounds **predictions of this frozen NN**, not measured consumption. Single-truck TSP alternatives retain their existing report-and-flag behavior; the reserve is enforced by the fleet VRP. Infeasible fleet stops are explicitly reported as unserved.

## Weight model

The default truck leaves at 13,000 lb, with four 20 kWh battery packs. Empty weight is 7,000 lb with four packs; adding/removing a pack changes empty weight by 200 kg (440.924 lb). Cargo is gross minus empty weight, spread evenly across the truck's assigned deliveries. A driver assignment never copies historical profile pack/weight fields into the truck.

## Versioning and limitations

Every optimize response includes `energy_model` with name, model version, SHA-256 and adapter version, plus resolved vehicles and driver snapshots. Send that metadata back as `expected_energy_model` on dependent requests; stale models are rejected instead of mixing old legs with new weights. Saved versions preserve their resolved driver defaults even if driver memory changes later.

The synthetic traces and driver distributions are modeling assumptions. Endpoint elevation does not capture every hill; the cruise factors do not model road safety limits; predicted Eco savings are not guaranteed. No measured vehicle accuracy or real-world battery guarantee is claimed. Charging/incident detours use the original leg's effective style and the returned road duration. The existing traffic playback and battery-drain demo remain simulations, not telemetry validation.
