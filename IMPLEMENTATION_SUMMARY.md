# Update Program Service - Easy UI Implementation

## Current Version: 2.0 (Global Valve Configuration)

The `rachio_local.update_program` service has been completely redesigned with an intuitive **global valve configuration** approach, making it dramatically easier to set up watering schedules.

## Key Features

### 1. Global Valve Configuration
- **Select valves once** - All runs use the same valves
- **Simple duration control**:
  - `total_duration`: Equal split across all valves (HH:MM:SS format)
  - `valve_duration_1`, `valve_duration_2`, etc.: Individual control per valve
- **No repetition** - Valves don't need to be specified per run

### 2. User-Friendly Time Format
- **HH:MM:SS format** instead of seconds
- Examples: `"00:15:00"` (15 min), `"01:30:00"` (1.5 hours)
- Also accepts **HH:MM** format (e.g., `"00:15"`)

### 3. Positional Valve Durations
- `valve_duration_1` matches the **first** valve selected
- `valve_duration_2` matches the **second** valve selected
- No need to type full entity IDs in a dictionary

### 4. Run-Specific Settings
- **`run_X_run_concurrently`**: Run all valves simultaneously instead of sequentially
- **`run_X_cycle_and_soak`**: Enable cycle and soak to prevent runoff
- Each run (1, 2, or 3) can have different settings

### 5. Flexible Update Modes
- **Full update**: Specify valves + run timing
- **Valve-only update**: Change valves without changing schedule
  - Preserves existing run times
  - Applies new valves and durations to all existing runs
  - Can still update `runConcurrently` and `cycleAndSoak` settings

## Benefits

✅ **Intuitive** - Global valve selection eliminates repetition  
✅ **Time-friendly** - HH:MM:SS format is easier to read than seconds  
✅ **Flexible** - Equal split or custom durations per valve  
✅ **Powerful** - Run-specific settings (concurrent, cycle & soak)  
✅ **Smart updates** - Can change valves without touching timing  
✅ **Entity pickers** - Select valves from dropdown  
✅ **Backwards compatible** - Advanced `runs` field still available  

## User Experience

### Before (Complex) ❌
```yaml
runs:
  - start_time: "06:00"
    valves:
      - entity_id: switch.valve_1
        duration: 600  # What's 600 seconds?
  - start_time: "18:00"
    valves:
      - entity_id: switch.valve_1
        duration: 600  # Repeated!
```

### After (Simple) ✅
```yaml
valves:
  - switch.valve_1
total_duration: "00:10:00"  # 10 minutes - clear!
run_1_start_time: "06:00"
run_2_start_time: "18:00"
```

## Implementation Details

### Files Modified

1. **`services.yaml`** (~20 intuitive UI fields)
   - Global: `valves`, `total_duration`, `valve_duration_1-4`
   - Per run: `run_X_start_time`, `run_X_sun_event`, `run_X_sun_offset`
   - Per run settings: `run_X_run_concurrently`, `run_X_cycle_and_soak`

2. **`__init__.py`**
   - `time_to_seconds()` helper function
   - Global entity runs building
   - Valve-only update mode support
   - Run-specific settings application

### Validation Rules

1. **Mode Exclusivity**: Easy UI XOR advanced `runs` field
2. **Start Type Exclusivity**: Fixed time XOR sun event per run
3. **Schedule Type Exclusivity**: ONE of days_of_week/interval_days/even_days/odd_days
4. **Duration Logic**: valve_duration_X OR total_duration split

### Valve-Only Updates

When valves specified but NO run timing:
1. Fetch existing program
2. Preserve all run timings
3. Update valves/durations in ALL runs
4. Apply any run settings if provided

## Technical Notes

### Time Parsing
- `"01:30:45"` → 5445 seconds
- `"01:30"` → 5400 seconds
- `300` → 300 seconds (backwards compatibility)

### Entity Resolution
- Smart Hose Timer: `{device_id}_{zone_id}_zone` → extract `zone_id`
- Controller: `{device_id}_valve_{valve_id}` → extract `valve_id`

### API Payload
```json
{
  "plannedRuns": {
    "runs": [{
      "fixedStart": {"startAt": {"hour": 6, "minute": 0, "second": 0}},
      "entityRuns": [
        {"entityId": "valve_id", "durationSec": "600"}
      ],
      "runConcurrently": false,
      "cycleAndSoak": true
    }]
  }
}
```

## Testing Checklist

- ✅ Equal duration split
- ✅ Custom durations per valve
- ✅ Fixed time runs
- ✅ Sun-based runs
- ✅ Multiple runs (2-3)
- ✅ Valve-only updates
- ✅ Run concurrently mode
- ✅ Cycle and soak mode
- ✅ Time format parsing
- ✅ Validation errors
- ✅ Advanced mode compatibility

---

**Last Updated**: October 13, 2025
