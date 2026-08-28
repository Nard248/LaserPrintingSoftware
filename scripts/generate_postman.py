import json
from pathlib import Path

# Postman v2.1.0 Collection definition for Labgate 2PP Control Platform
collection = {
    "info": {
        "_postman_id": "labgate-2pp-control-platform-v1",
        "name": "Labgate 2PP Platform API Collection",
        "description": "Complete Postman collection for testing the Labgate 2PP Laser Printing Platform local API. Covers system health, capability grounding, device status, single-command execution, multi-op experiment sweeps, plan approval lifecycle, dry-run previews, aborts, and 3D STL model uploads.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    },
    "auth": {
        "type": "bearer",
        "bearer": [
            {
                "key": "token",
                "value": "{{operatorToken}}",
                "type": "string"
            }
        ]
    },
    "variable": [
        {
            "key": "baseUrl",
            "value": "http://127.0.0.1:8523",
            "type": "string"
        }
    ],
    "item": [
        {
            "name": "01 - System & Discovery",
            "item": [
                {
                    "name": "Get Health Status",
                    "request": {
                        "auth": {
                            "type": "noauth"
                        },
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/health",
                            "host": ["{{baseUrl}}"],
                            "path": ["health"]
                        },
                        "description": "Check platform liveness, version, and execution mode (sim or rig)."
                    }
                },
                {
                    "name": "Get Hardware Capabilities (Grounding)",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/capabilities",
                            "host": ["{{baseUrl}}"],
                            "path": ["capabilities"]
                        },
                        "description": "Query machine-readable grounding bounds, devices, and allowed operation types."
                    }
                },
                {
                    "name": "Get Live Device States",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/devices",
                            "host": ["{{baseUrl}}"],
                            "path": ["devices"]
                        },
                        "description": "Get real-time live connection status and coordinates for active devices."
                    }
                }
            ]
        },
        {
            "name": "02 - Single Command Execution",
            "item": [
                {
                    "name": "Single Command - Move Stage",
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "if (pm.response.code === 201) {",
                                    "    var data = pm.response.json();",
                                    "    pm.environment.set('planId', data.plan_id);",
                                    "}"
                                ],
                                "type": "text/javascript"
                            }
                        }
                    ],
                    "request": {
                        "method": "POST",
                        "header": [
                            {"key": "Content-Type", "value": "application/json"}
                        ],
                        "body": {
                            "mode": "raw",
                            "raw": json.dumps({
                                "spec": {
                                    "spec_version": "1.0",
                                    "title": "Single Command: Move Stage to (1.0, 2.0, 6.0)",
                                    "description": "Direct single stage move test",
                                    "operations": [
                                        {
                                            "op": "move_stage",
                                            "target_mm": [1.0, 2.0, 6.0]
                                        }
                                    ]
                                }
                            }, indent=2)
                        },
                        "url": {
                            "raw": "{{baseUrl}}/plans",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans"]
                        },
                        "description": "Submit a single-command plan to move the stage to target (X=1mm, Y=2mm, Z=6mm)."
                    }
                },
                {
                    "name": "Single Command - Set Laser Power",
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "if (pm.response.code === 201) {",
                                    "    var data = pm.response.json();",
                                    "    pm.environment.set('planId', data.plan_id);",
                                    "}"
                                ],
                                "type": "text/javascript"
                            }
                        }
                    ],
                    "request": {
                        "method": "POST",
                        "header": [
                            {"key": "Content-Type", "value": "application/json"}
                        ],
                        "body": {
                            "mode": "raw",
                            "raw": json.dumps({
                                "spec": {
                                    "spec_version": "1.0",
                                    "title": "Single Command: Set Laser Power 25%",
                                    "description": "Direct laser power command",
                                    "operations": [
                                        {
                                            "op": "set_laser_power",
                                            "attenuator_percent": 25.0,
                                            "pp_divider": 1
                                        }
                                    ]
                                }
                            }, indent=2)
                        },
                        "url": {
                            "raw": "{{baseUrl}}/plans",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans"]
                        },
                        "description": "Submit a single-command plan to configure attenuator to 25%."
                    }
                },
                {
                    "name": "Single Command - Write Single Line",
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "if (pm.response.code === 201) {",
                                    "    var data = pm.response.json();",
                                    "    pm.environment.set('planId', data.plan_id);",
                                    "}"
                                ],
                                "type": "text/javascript"
                            }
                        }
                    ],
                    "request": {
                        "method": "POST",
                        "header": [
                            {"key": "Content-Type", "value": "application/json"}
                        ],
                        "body": {
                            "mode": "raw",
                            "raw": json.dumps({
                                "spec": {
                                    "spec_version": "1.0",
                                    "title": "Single Command: Write Single Line",
                                    "description": "Expose a 4mm line from (-2,0,6) to (2,0,6)",
                                    "operations": [
                                        {
                                            "op": "set_laser_power",
                                            "attenuator_percent": 20.0,
                                            "pp_divider": 1
                                        },
                                        {
                                            "op": "write_line",
                                            "start_mm": [-2.0, 0.0, 6.0],
                                            "end_mm": [2.0, 0.0, 6.0],
                                            "velocity_mm_s": 5.0,
                                            "repetitions": 1
                                        }
                                    ]
                                }
                            }, indent=2)
                        },
                        "url": {
                            "raw": "{{baseUrl}}/plans",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans"]
                        },
                        "description": "Submit a single-line print recipe."
                    }
                },
                {
                    "name": "Single Command - Capture Inspection Image",
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "if (pm.response.code === 201) {",
                                    "    var data = pm.response.json();",
                                    "    pm.environment.set('planId', data.plan_id);",
                                    "}"
                                ],
                                "type": "text/javascript"
                            }
                        }
                    ],
                    "request": {
                        "method": "POST",
                        "header": [
                            {"key": "Content-Type", "value": "application/json"}
                        ],
                        "body": {
                            "mode": "raw",
                            "raw": json.dumps({
                                "spec": {
                                    "spec_version": "1.0",
                                    "title": "Single Command: Capture Image",
                                    "description": "Capture inspection photo of sample area",
                                    "operations": [
                                        {
                                            "op": "capture_image",
                                            "label": "manual_inspection",
                                            "wl_on": True
                                        }
                                    ]
                                }
                            }, indent=2)
                        },
                        "url": {
                            "raw": "{{baseUrl}}/plans",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans"]
                        },
                        "description": "Capture an inspection photo with white-light auto-toggle."
                    }
                }
            ]
        },
        {
            "name": "03 - Full Plan Lifecycle (Multi-Op Sweep)",
            "item": [
                {
                    "name": "Submit Power Sweep Array Plan",
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "if (pm.response.code === 201) {",
                                    "    var data = pm.response.json();",
                                    "    pm.environment.set('planId', data.plan_id);",
                                    "}"
                                ],
                                "type": "text/javascript"
                            }
                        }
                    ],
                    "request": {
                        "method": "POST",
                        "header": [
                            {"key": "Content-Type", "value": "application/json"}
                        ],
                        "body": {
                            "mode": "raw",
                            "raw": json.dumps({
                                "spec": {
                                    "spec_version": "1.0",
                                    "title": "Power sweep 3 lines + camera inspection",
                                    "description": "Sweep 10%, 20%, 30% attenuator power over three 4mm lines and capture image.",
                                    "operations": [
                                        {
                                            "op": "write_power_sweep_array",
                                            "x_start_mm": -2.0,
                                            "x_end_mm": 2.0,
                                            "y_start_mm": 0.0,
                                            "y_pitch_mm": 0.1,
                                            "attenuator_percent_per_line": [10.0, 20.0, 30.0],
                                            "z_mm": 6.0,
                                            "velocity_mm_s": 5.0
                                        },
                                        {
                                            "op": "capture_image",
                                            "label": "sweep_result",
                                            "wl_on": True
                                        }
                                    ]
                                }
                            }, indent=2)
                        },
                        "url": {
                            "raw": "{{baseUrl}}/plans",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans"]
                        },
                        "description": "Submit a multi-operation recipe (WritePowerSweepArray + CaptureImage)."
                    }
                },
                {
                    "name": "Dry-Run & Render Toolpath Preview",
                    "request": {
                        "method": "POST",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/plans/{{planId}}/dry-run",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans", "{{planId}}", "dry-run"]
                        },
                        "description": "Estimate duration, travel distance, exposure count, and render toolpath preview PNG."
                    }
                },
                {
                    "name": "Approve Plan (Approver Token)",
                    "request": {
                        "auth": {
                            "type": "bearer",
                            "bearer": [
                                {
                                    "key": "token",
                                    "value": "{{approverToken}}",
                                    "type": "string"
                                }
                            ]
                        },
                        "method": "POST",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/plans/{{planId}}/approve",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans", "{{planId}}", "approve"]
                        },
                        "description": "Authorize plan for execution. Enforces proposer != approver identity check."
                    }
                },
                {
                    "name": "Execute Plan",
                    "request": {
                        "method": "POST",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/plans/{{planId}}/execute",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans", "{{planId}}", "execute"]
                        },
                        "description": "Enqueue approved plan to execution worker queue."
                    }
                },
                {
                    "name": "Get Execution Queue",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/queue",
                            "host": ["{{baseUrl}}"],
                            "path": ["queue"]
                        },
                        "description": "View currently running plan and queued plans."
                    }
                },
                {
                    "name": "Get Plan Status & History",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/plans/{{planId}}",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans", "{{planId}}"]
                        },
                        "description": "Poll plan state (queued -> running -> completed) and history."
                    }
                },
                {
                    "name": "Get Plan Results Manifest & Telemetry",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/plans/{{planId}}/results",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans", "{{planId}}", "results"]
                        },
                        "description": "Retrieve telemetry log events and generated artifact file list."
                    }
                },
                {
                    "name": "Download Artifact (e.g. sweep_result.png)",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/plans/{{planId}}/results/artifacts/sweep_result.png",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans", "{{planId}}", "results", "artifacts", "sweep_result.png"]
                        },
                        "description": "Download captured image or preview artifact file."
                    }
                },
                {
                    "name": "Rerun / Clone Plan",
                    "request": {
                        "method": "POST",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/plans/{{planId}}/rerun",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans", "{{planId}}", "rerun"]
                        },
                        "description": "Clone an existing plan specification into a new plan."
                    }
                },
                {
                    "name": "Abort Plan Execution",
                    "request": {
                        "method": "POST",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/plans/{{planId}}/abort",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans", "{{planId}}", "abort"]
                        },
                        "description": "Request cooperative abort of a queued or running plan."
                    }
                }
            ]
        },
        {
            "name": "04 - 3D Printing & STL Management",
            "item": [
                {
                    "name": "Upload STL Model",
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "if (pm.response.code === 201) {",
                                    "    var data = pm.response.json();",
                                    "    pm.environment.set('modelId', data.model_id);",
                                    "}"
                                ],
                                "type": "text/javascript"
                            }
                        }
                    ],
                    "request": {
                        "method": "POST",
                        "header": [],
                        "body": {
                            "mode": "formdata",
                            "formdata": [
                                {
                                    "key": "file",
                                    "type": "file",
                                    "src": "labgate_data/models/cube_100.stl"
                                }
                            ]
                        },
                        "url": {
                            "raw": "{{baseUrl}}/models",
                            "host": ["{{baseUrl}}"],
                            "path": ["models"]
                        },
                        "description": "Upload a 3D STL file to obtain a content-addressed model_id."
                    }
                },
                {
                    "name": "List Uploaded STL Models",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/models",
                            "host": ["{{baseUrl}}"],
                            "path": ["models"]
                        },
                        "description": "List all uploaded STL models stored on the server."
                    }
                },
                {
                    "name": "Get Model Info",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {
                            "raw": "{{baseUrl}}/models/{{modelId}}",
                            "host": ["{{baseUrl}}"],
                            "path": ["models", "{{modelId}}"]
                        },
                        "description": "Get metadata, dimensions, and point count for model."
                    }
                },
                {
                    "name": "Submit Print STL Plan",
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "if (pm.response.code === 201) {",
                                    "    var data = pm.response.json();",
                                    "    pm.environment.set('planId', data.plan_id);",
                                    "}"
                                ],
                                "type": "text/javascript"
                            }
                        }
                    ],
                    "request": {
                        "method": "POST",
                        "header": [
                            {"key": "Content-Type", "value": "application/json"}
                        ],
                        "body": {
                            "mode": "raw",
                            "raw": json.dumps({
                                "spec": {
                                    "spec_version": "1.0",
                                    "title": "3D Print STL Model",
                                    "description": "Slice and print uploaded cube_100 STL model",
                                    "operations": [
                                        {
                                            "op": "print_stl",
                                            "model_id": "{{modelId}}",
                                            "unit": "micron",
                                            "step_size": 5.0,
                                            "start_position_mm": [0.0, 0.0, 6.0],
                                            "attenuator_percent": 25.0,
                                            "velocity_mm_s": 5.0,
                                            "pp_divider": 1,
                                            "repetition_count": 1,
                                            "is_ablation": False
                                        }
                                    ]
                                }
                            }, indent=2)
                        },
                        "url": {
                            "raw": "{{baseUrl}}/plans",
                            "host": ["{{baseUrl}}"],
                            "path": ["plans"]
                        },
                        "description": "Submit a 3D STL printing plan referencing the uploaded model_id."
                    }
                }
            ]
        }
    ]
}

environment = {
    "id": "labgate-local-environment",
    "name": "Labgate Local Environment",
    "values": [
        {
            "key": "baseUrl",
            "value": "http://127.0.0.1:8523",
            "type": "default",
            "enabled": True
        },
        {
            "key": "operatorToken",
            "value": "CHANGE-ME-operator-token",
            "type": "secret",
            "enabled": True
        },
        {
            "key": "approverToken",
            "value": "CHANGE-ME-approver-token",
            "type": "secret",
            "enabled": True
        },
        {
            "key": "planId",
            "value": "",
            "type": "default",
            "enabled": True
        },
        {
            "key": "modelId",
            "value": "",
            "type": "default",
            "enabled": True
        }
    ],
    "_postman_variable_scope": "environment"
}

for target_dir in [Path("postman"), Path("Docs/postman")]:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "labgate.postman_collection.json").write_text(json.dumps(collection, indent=2))
    (target_dir / "labgate.postman_environment.json").write_text(json.dumps(environment, indent=2))
print("Postman collection and environment generated successfully in postman/ and Docs/postman/.")
