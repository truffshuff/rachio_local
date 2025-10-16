# Create Program Implementation

## Overview
The `create_program` service allows users to create new watering programs on their Rachio Smart Hose Timer devices through Home Assistant.

## Required Fields

### Essential Program Information
- **device_id** (text): The device ID of the Smart Hose Timer
- **name** (text): Name for the new program (e.g., "Morning Watering")

### Program Schedule Dates
- **start_on_year**, **start_on_month**, **start_on_day**: When the program schedule becomes active
- **end_on_year**, **end_on_month**, **end_on_day**: When the program schedule ends

### Schedule Type (Choose ONE)
- **days_of_week** (multi-select): Specific days the program runs (e.g., Monday, Wednesday, Friday)
  - OR -
- **interval_days** (number): Days between runs (e.g., every 3 days)

### Run Configuration (At Least One Run Required)
**Each run must have:**
1. **Start timing** (choose one):
   - `run_X_start_time`: Fixed time (e.g., "06:00")
   - `run_X_sun_event` + `run_X_sun_offset`: Sun-based (e.g., AFTER_SUNRISE with 30 minute offset)

2. **Valves**: At least one valve must be selected in the global `valves` field

### Valve Configuration
- **valves** (entity multi-select): Select one or more valves/zones to water
- **Durations** (choose one):
  - `valve_duration_1`, `valve_duration_2`, etc.: Individual durations per valve (HH:MM:SS format)
  - `total_duration`: Total time split equally across all valves (HH:MM:SS format)

## Optional Fields
- **enabled** (boolean): Enable/disable the program (default: true)
- **rain_skip_enabled** (boolean): Enable rain skip feature (default: true)
- **color** (RGB color): Program color in Home Assistant UI
- **run_X_run_concurrently** (boolean): Run all valves simultaneously vs sequentially
- **run_X_cycle_and_soak** (boolean): Enable cycle and soak feature

## Example: Minimal Create Program

```yaml
service: rachio_local.create_program
data:
  device_id: "abc123def456"
  name: "Summer Evening Watering"
  
  # When active
  start_on_year: 2024
  start_on_month: 5
  start_on_day: 1
  end_on_year: 2024
  end_on_month: 9
  end_on_day: 30
  
  # Schedule: Monday, Wednesday, Friday
  days_of_week:
    - monday
    - wednesday
    - friday
  
  # One run at 6:00 PM
  run_1_start_time: "18:00"
  
  # Water two zones for 10 minutes each
  valves:
    - switch.zone_1
    - switch.zone_2
  total_duration: "00:20:00"
```

## Example: Multiple Runs with Individual Durations

```yaml
service: rachio_local.create_program
data:
  device_id: "abc123def456"
  name: "Two-Phase Morning Watering"
  
  # When active
  start_on_year: 2024
  start_on_month: 3
  start_on_day: 15
  end_on_year: 2024
  end_on_month: 10
  end_on_day: 31
  
  # Schedule: Every 2 days
  interval_days: 2
  
  # First run at sunrise
  run_1_sun_event: "AFTER_SUNRISE"
  run_1_sun_offset: 15
  
  # Second run 2 hours later
  run_2_start_time: "08:00"
  
  # Water three zones with different durations
  valves:
    - switch.front_yard_zone_1
    - switch.front_yard_zone_2
    - switch.backyard_zone_1
  valve_duration_1: "00:15:00"  # 15 minutes
  valve_duration_2: "00:10:00"  # 10 minutes
  valve_duration_3: "00:20:00"  # 20 minutes
  
  # Optional settings
  rain_skip_enabled: true
  color: [0, 167, 225]  # Blue
```

## API Details

### Endpoint
`POST /program/createProgramV2`

### Payload Structure
```json
{
  "deviceId": "abc123def456",
  "name": "Program Name",
  "enabled": true,
  "rainSkipEnabled": true,
  "color": "#00A7E1",
  "startOn": {
    "year": 2024,
    "month": 5,
    "day": 1
  },
  "endOn": {
    "year": 2024,
    "month": 9,
    "day": 30
  },
  "daysOfWeek": {
    "daysOfWeek": ["MONDAY", "WEDNESDAY", "FRIDAY"]
  },
  "plannedRuns": {
    "runs": [
      {
        "fixedStart": {
          "startAt": {
            "hour": 18,
            "minute": 0,
            "second": 0
          }
        },
        "entityRuns": [
          {
            "entityId": "zone_1",
            "durationSec": "600"
          },
          {
            "entityId": "zone_2",
            "durationSec": "600"
          }
        ],
        "runConcurrently": false,
        "cycleAndSoak": false
      }
    ]
  }
}
```

## Validation Rules

1. **Mutually Exclusive Scheduling**: Cannot use both `days_of_week` AND `interval_days`
2. **Mutually Exclusive Run Start**: Each run cannot have both `start_time` AND `sun_event`
3. **Complete Date Fields**: Must provide all 3 date components (year, month, day) for both start and end
4. **At Least One Run**: Must configure at least one run with timing
5. **At Least One Valve**: Must select at least one valve in the global valves field

## Key Differences from Update Program

| Feature | create_program | update_program |
|---------|---------------|---------------|
| Program Identifier | `device_id` (text) | `program_id` (entity selector) |
| Name Field | Required | Optional |
| Date Fields | Required | Optional |
| Schedule Type | Required | Optional |
| Run Configuration | Required | Optional |
| API Method | POST | PUT |
| API Endpoint | `/program/createProgramV2` | `/program/updateProgramV2` |
| Valve-Only Updates | Not applicable | Supported |

## After Creation

After successfully creating a program:
1. The integration refreshes the device data
2. A new program sensor entity appears (e.g., `sensor.smart_hose_timer_program_summer_evening_watering`)
3. The program can be modified using the `update_program` service
4. The program can be enabled/disabled using the `enable_program`/`disable_program` services

## Error Handling

The service will log errors and abort if:
- Device ID is missing or invalid
- Device is not a Smart Hose Timer
- Required fields are missing (name, dates, schedule, runs, valves)
- Multiple scheduling types are specified
- Date fields are incomplete
- API returns an error (logged with status code and error message)
