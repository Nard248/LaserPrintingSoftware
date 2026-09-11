# Bringing the rig up over the API

Everything below is one HTTP call at a time. Nothing here needs a plan or a
second person's approval — that ceremony is reserved for firing the laser.

Set up a shell first:

```bash
BASE=http://127.0.0.1:8523
OP="Authorization: Bearer <operator token>"
```

## 1. Ask what is wrong before touching anything

```bash
curl -s -H "$OP" $BASE/system/preflight | jq
```

This is the call to reach for first, every time. It returns a checklist; each
failing entry carries a `remedy`. Two kinds:

- `"remedy": "POST /devices/stage/actions/enable_axes"` — do it over the API.
- `"manual": true` — go and do it with your hands. Turning the key switch on
  the laser head, seating an Ethernet cable, clearing an enclosure interlock:
  no software can do these, so the platform names them instead of failing
  vaguely.

`manual_steps` collects the physical ones in one list.

## 2. Bring each device up

```bash
curl -s -X POST -H "$OP" $BASE/devices/stage/connect          # opens the SPiiPlus
                                                              # link, enables and
                                                              # commutates the axes
curl -s -X POST -H "$OP" $BASE/devices/laser/connect
curl -s -X POST -H "$OP" $BASE/devices/camera/connect
curl -s -X POST -H "$OP" $BASE/devices/white_light/connect
```

If a device misbehaves later, `POST /devices/{id}/diagnose` probes it without
actuating anything.

The axes can be re-enabled after a fault without tearing the link down:

```bash
curl -s -X POST -H "$OP" -H 'Content-Type: application/json' \
     -d '{"params":{}}' $BASE/devices/stage/actions/enable_axes
```

## 3. Find out what each device will accept

```bash
curl -s -H "$OP" $BASE/devices/stage/actions | jq '.[] | {name, tier}'
```

Every action lists its parameters with units, minima and maxima. That is the
same declaration the validator enforces, so there is no second copy to drift.

## 4. Drive it

```bash
JSON='Content-Type: application/json'
A=$BASE/devices/stage/actions

curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{}}'                      $A/home
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{"velocity_mm_s":3.0}}'   $A/set_velocity
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{"axis":2,"distance_mm":0.1}}' $A/jog
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{}}'                      $A/position
```

Laser — note that `set_power` *arms* the laser; it does not open the shutter:

```bash
L=$BASE/devices/laser/actions
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{}}' $L/status      # errors, warnings,
                                                                      # measured power
curl -s -X POST -H "$OP" -H "$JSON" \
     -d '{"params":{"attenuator_percent":30,"pp_divider":1}}' $L/set_power
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{}}' $L/output_off
```

Camera:

```bash
C=$BASE/devices/camera/actions
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{}}' $C/settings
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{"exposure_time_us":30000}}' $C/set_exposure
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{"label":"align"}}' $C/snapshot
# -> {"data": {"url": "/snapshots/camera_align_....png"}}
curl -s -H "$OP" $BASE/snapshots/camera_align_....png -o align.png
```

## 5. When something is wrong

```bash
curl -s -X POST -H "$OP" -H "$JSON" -d '{"params":{}}' \
     $BASE/devices/stage/actions/halt          # stop the stage
curl -s -X POST -H "$OP" $BASE/system/estop    # abort the run and safe-state
                                               # everything, laser first
```

`halt`, `output_off` and `/system/estop` are never interlocked out. Every
other action can be refused; a stop cannot.

## 6. Firing the laser by hand (admin only)

Physically testing a rig means firing the beam without filing a plan first.
That is the `expose` tier, and it needs the **`admin`** role:

```bash
ADM="Authorization: Bearer <admin token>"
L=$BASE/devices/laser/actions

curl -s -X POST -H "$ADM" -H "$JSON" \
     -d '{"params":{"attenuator_percent":25}}' $L/set_power   # arm
curl -s -X POST -H "$ADM" -H "$JSON" -d '{"params":{}}' $L/output_on   # BEAM LIVE
curl -s -X POST -H "$ADM" -H "$JSON" \
     -d '{"params":{"axis":2,"distance_mm":0.2}}' $BASE/devices/stage/actions/jog
curl -s -X POST -H "$ADM" -H "$JSON" -d '{"params":{}}' $L/output_off
```

An **operator** token gets `403` on `output_on` and `502` on jogging while
the beam is live. An **admin** may do both: the beam-on motion interlock is
overridden for admin, because otherwise opening the shutter would leave the
stage frozen and manual testing impossible.

Neither is silent. Opening the shutter outside a plan writes
`beam_opened_manually` to the audit log, and moving with the beam live writes
`interlock_override`, both naming the person.

One thing admin does **not** override: exclusivity with a running plan. That
is a correctness invariant rather than a policy — interleaving with the
executor can corrupt an exposure — so a device action during a run is still
`409`. `halt` and `/system/estop` remain available to stop the run first.

## 7. Then run an actual experiment

Once preflight is green, the plan path is unchanged: `POST /plans` →
`POST /plans/{id}/dry-run` → a second person approves →
`POST /plans/{id}/execute`.

---

## What this path will refuse, and why

| You try | You get | Because |
| --- | --- | --- |
| jog before connecting | `502` | the device is not connected |
| jog before `enable_axes` | `502` | the servo axes are not commutated — this is the usual reason "the stage does not move" |
| jog 6 mm when the clamp is 5 mm | `422` | interactive moves are bounded; long travel belongs in a plan |
| jog while the beam is on | `502` | motion is interlocked against exposure |
| jog while a plan is running | `409` | the execution engine owns the rig |
| open the shutter as an operator | `403` | `expose` needs the `admin` role (or `allow_manual_beam`) |
| jog with only the approver role | `403` | motion needs `operator` |
| any device action, as admin, during a run | `409` | correctness, not policy — admin does not override this |

An unreachable laser counts as *possibly on*, so motion is refused rather
than allowed. That is deliberate: the conservative reading is the safe one.
