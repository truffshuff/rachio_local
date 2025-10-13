# Service Usage Examples

## Update Program Service

The `rachio_local.update_program` service allows you to update Smart Hose Timer program settings. This document provides examples of how to use the service with the latest UI improvements.

### Easy UI Mode (Recommended)

The service includes easy-to-use UI fields with **global valve configuration** and up to 3 runs per day. This is the recommended approach for most users.

#### Example 1: Single Run with Fixed Time and Equal Duration Split

Configure a program to run once per day at 6:00 AM with two valves sharing time equally:

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_morning_watering
  enabled: true
  # Global valve selection
  valves:
    - switch.smart_hose_timer_valve_front_yard
    - switch.smart_hose_timer_valve_back_yard
  total_duration: "00:10:00"  # 10 minutes total = 5 min per valve
  # Run 1 timing
  run_1_start_time: "06:00"
  # Schedule
  days_of_week:
    - monday
    - wednesday
    - friday
```

#### Example 2: Individual Valve Durations

Different durations for each valve:

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_morning_watering
  # Global valve selection (in order)
  valves:
    - switch.smart_hose_timer_valve_front_yard    # Valve 1
    - switch.smart_hose_timer_valve_back_yard     # Valve 2
  # Individual durations
  valve_duration_1: "00:15:00"  # Front yard: 15 minutes
  valve_duration_2: "00:08:00"  # Back yard: 8 minutes
  # Run timing
  run_1_start_time: "06:00"
  interval_days: 3
```

#### Example 3: Two Runs Per Day with Same Valves

Morning and evening watering using the same valves:

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_daily_watering
  enabled: true
  # Global valves (used by both runs)
  valves:
    - switch.smart_hose_timer_valve_garden
    - switch.smart_hose_timer_valve_lawn
  valve_duration_1: "00:10:00"  # Garden: 10 minutes
  valve_duration_2: "00:15:00"  # Lawn: 15 minutes
  # Morning run at 6:00 AM
  run_1_start_time: "06:00"
  # Evening run at 6:00 PM
  run_2_start_time: "18:00"
  # Schedule
  days_of_week:
    - monday
    - tuesday
    - wednesday
    - thursday
    - friday
```

#### Example 4: Sun-Based Timing

Water after sunset:

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_evening_watering
  enabled: true
  valves:
    - switch.smart_hose_timer_valve_garden
    - switch.smart_hose_timer_valve_lawn
  total_duration: "00:20:00"  # Split equally: 10 min each
  run_1_sun_event: "AFTER_SET"
  run_1_sun_offset: 30  # 30 minutes after sunset
  interval_days: 2
```

#### Example 5: Run Concurrently (Both Valves at Once)

Water both valves at the same time instead of sequentially:

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_quick_water
  valves:
    - switch.smart_hose_timer_valve_front
    - switch.smart_hose_timer_valve_back
  total_duration: "00:15:00"  # Each valve runs for 15 min simultaneously
  run_1_start_time: "06:00"
  run_1_run_concurrently: true  # Both valves run at same time
  interval_days: 3
```

#### Example 6: Cycle and Soak

Enable cycle and soak to prevent runoff:

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_slope_watering
  valves:
    - switch.smart_hose_timer_valve_hillside
  total_duration: "00:30:00"
  run_1_start_time: "05:00"
  run_1_cycle_and_soak: true  # Breaks into cycles with soak time
  days_of_week:
    - monday
    - thursday
```

#### Example 7: Three Runs Per Day with Different Settings

Morning, midday, and evening with varying configurations:

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_summer_intensive
  enabled: true
  # Global valves
  valves:
    - switch.smart_hose_timer_valve_vegetable_garden
    - switch.smart_hose_timer_valve_flower_beds
  valve_duration_1: "00:12:00"  # Vegetables: 12 minutes
  valve_duration_2: "00:08:00"  # Flowers: 8 minutes
  
  # Morning - before sunrise with cycle and soak
  run_1_sun_event: "BEFORE_RISE"
  run_1_sun_offset: -30  # 30 minutes before sunrise
  run_1_cycle_and_soak: true
  
  # Midday - quick concurrent watering
  run_2_start_time: "12:00"
  run_2_run_concurrently: true
  
  # Evening - after sunset normal watering
  run_3_sun_event: "AFTER_SET"
  run_3_sun_offset: 15  # 15 minutes after sunset
  
  even_days: true  # Run on even days of the month
```

#### Example 8: Update Only Valves (Keep Existing Schedule)

Change which valves are used without changing run times:

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_existing
  # Just specify new valves - existing run times are preserved
  valves:
    - switch.smart_hose_timer_valve_new_zone_1
    - switch.smart_hose_timer_valve_new_zone_2
  valve_duration_1: "00:10:00"
  valve_duration_2: "00:15:00"
```
    - switch.smart_hose_timer_valve_back_yard
  run_3_valve_duration: 900
  even_days: true  # Run on even days of the month
```

### Advanced Mode

For more complex configurations (4+ runs per day, or per-valve duration control within runs), you can still use the advanced `runs` field with YAML/JSON:

#### Example 9: Advanced YAML Configuration

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_custom
  enabled: true
  runs:
    - start_time: "05:00"
      valves:
        - entity_id: switch.smart_hose_timer_valve_1
          duration: 300
        - entity_id: switch.smart_hose_timer_valve_2
          duration: 450  # Different duration for valve 2
    - start_time: "17:00"
      valves:
        - entity_id: switch.smart_hose_timer_valve_3
          duration: 600
    - sun_event: "AFTER_SET"
      sun_offset_minutes: 30
      valves:
        - entity_id: switch.smart_hose_timer_valve_4
          duration: 900
  days_of_week:
    - monday
    - wednesday
    - friday
```

### Important Notes

1. **Don't Mix Modes**: Do not use both the easy UI fields (`valves`, `run_1_*`, etc.) and the advanced `runs` field in the same service call. Choose one or the other.

2. **Global Valves**: All runs use the same valves. Specify valves once at the top level, then configure timing for each run.

3. **Duration Formats**:
   - **HH:MM:SS** format (e.g., "00:15:00" for 15 minutes)
   - Accepts **HH:MM** format too (e.g., "00:15")

4. **Valve Durations**:
   - Use `total_duration` for equal split across all valves (simple)
   - Use `valve_duration_1`, `valve_duration_2`, etc. for individual control (valves are assigned in the order you selected them)

5. **Start Time Options**: For each run, use either:
   - `run_X_start_time` for fixed time (e.g., "06:00")
   - `run_X_sun_event` + `run_X_sun_offset` for sun-based timing
   - Do NOT use both for the same run

6. **Run Settings Per Run**:
   - `run_X_run_concurrently`: Run all valves at the same time (default: false)
   - `run_X_cycle_and_soak`: Enable cycle and soak mode (default: false)

7. **Schedule Types**: Only ONE scheduling type can be used per program:
   - `days_of_week` (for weekly schedules)
   - `interval_days` (for every N days)
   - `even_days` (for even calendar days)
   - `odd_days` (for odd calendar days)

8. **Valve-Only Updates**: You can update just the valves without specifying run timing. The existing schedule will be preserved with new valves applied to all runs.

### Other Service Fields

You can combine run configuration with these other program settings:

- `enabled`: `true` or `false` - Enable or disable the program
- `name`: Change the program name
- `rain_skip_enabled`: `true` or `false` - Enable/disable rain skip
- `color`: Program color as hex (e.g., "#00A7E1") or RGB list

### Full Example with All Options

```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_main
  enabled: true
  name: "Main Garden Program"
  rain_skip_enabled: true
  color: "#00A7E1"
  
  # Global valve configuration
  valves:
    - switch.smart_hose_timer_valve_front_lawn
    - switch.smart_hose_timer_valve_side_yard
    - switch.smart_hose_timer_valve_back_garden
  # Individual durations (in order of valve selection)
  valve_duration_1: "00:10:00"  # Front lawn: 10 min
  valve_duration_2: "00:08:00"  # Side yard: 8 min
  valve_duration_3: "00:15:00"  # Back garden: 15 min
  
  # Morning run - sequential with cycle and soak
  run_1_start_time: "06:30"
  run_1_cycle_and_soak: true
  
  # Evening run - concurrent watering
  run_2_start_time: "19:00"
  run_2_run_concurrently: true
  
  # Schedule
  days_of_week:
    - monday
    - wednesday
    - friday
    - sunday
```

## Testing Your Configuration

1. Start with a simple single-run configuration
2. Check the program sensor attributes to verify the configuration was applied correctly
3. Use the Home Assistant Developer Tools > Services UI to test configurations before creating automations
4. Check Home Assistant logs for any error messages if the configuration doesn't apply as expected

## Tips and Best Practices

- **Equal Watering**: Use `total_duration` when all valves need the same amount of water
- **Custom Watering**: Use `valve_duration_X` when different zones need different amounts
- **Concurrent vs Sequential**: Use `run_concurrently: true` when valves can run together without water pressure issues
- **Cycle and Soak**: Enable on slopes or clay soil to prevent runoff
- **Valve Order Matters**: When using `valve_duration_1`, `valve_duration_2`, etc., they match the order you selected valves in the `valves` field

