# Quick Reference: Create Program Minimal Requirements

## What You MUST Provide

### 1. Basic Info
- ✅ `name` - A name for your program

### 2. Season Dates
- ✅ `start_on_date` - When program becomes active (date picker: YYYY-MM-DD)
- ✅ `end_on_date` - When program ends (date picker: YYYY-MM-DD)

### 3. Schedule (Pick ONE)
- ✅ `days_of_week` - Run on specific days (e.g., Mon, Wed, Fri)
  **OR**
- ✅ `interval_days` - Run every X days (e.g., every 3 days)

### 4. At Least One Run with Timing
- ✅ `run_1_start_time` - Fixed time like "06:00"
  **OR**
- ✅ `run_1_sun_event` + `run_1_sun_offset` - Sunrise/sunset based

### 5. At Least One Valve
- ✅ `valves` - Select one or more zones to water
- ✅ Duration: Either `total_duration` OR individual `valve_duration_X` fields

## What's Optional

You can change these later with `update_program`:
- ❌ `rain_skip_enabled` (default: true - enabled by default)
- ❌ `color` (default: Rachio blue)
- ❌ `run_X_run_concurrently` (default: false)
- ❌ `run_X_cycle_and_soak` (default: false)

**Note:** Programs are automatically enabled when created. Use `rachio_local.enable_program` or `rachio_local.disable_program` to control them after creation.

## Simplest Possible Example

```yaml
service: rachio_local.create_program
data:
  # 1. Basic
  name: "Simple Program"
  
  # 2. Dates (May 1 - Sept 30, 2024)
  start_on_date: "2024-05-01"
  end_on_date: "2024-09-30"
  
  # 3. Schedule (Every Monday, Wednesday, Friday)
  days_of_week:
    - monday
    - wednesday
    - friday
  
  # 4. Run (6 AM)
  run_1_start_time: "06:00"
  
  # 5. Valve (10 minutes)
  valves: switch.zone_1
  total_duration: "00:10:00"
```

That's it! The absolute minimum to create a working program.

## Common Patterns

### Pattern 1: Single Zone, Fixed Time
```yaml
days_of_week: ["monday", "wednesday", "friday"]
run_1_start_time: "18:00"
valves: switch.zone_1
total_duration: "00:15:00"
```

### Pattern 2: Multiple Zones, Equal Split
```yaml
interval_days: 2
run_1_start_time: "06:00"
valves:
  - switch.zone_1
  - switch.zone_2
  - switch.zone_3
total_duration: "00:30:00"  # 10 min each
```

### Pattern 3: Multiple Zones, Custom Duration
```yaml
days_of_week: ["tuesday", "thursday", "saturday"]
run_1_start_time: "19:00"
valves:
  - switch.front_lawn
  - switch.back_lawn
  - switch.garden
valve_duration_1: "00:20:00"  # Front needs more
valve_duration_2: "00:15:00"  # Back needs less
valve_duration_3: "00:10:00"  # Garden needs least
```

### Pattern 4: Sunrise Based
```yaml
interval_days: 3
run_1_sun_event: "AFTER_SUNRISE"
run_1_sun_offset: 30  # 30 minutes after sunrise
valves: switch.zone_1
total_duration: "00:15:00"
```

### Pattern 5: Two Runs Per Day
```yaml
days_of_week: ["sunday", "wednesday"]
run_1_start_time: "06:00"  # Morning
run_2_start_time: "18:00"  # Evening
valves: switch.backyard_zone
total_duration: "00:20:00"  # 10 min each run
```

## Validation Errors to Avoid

❌ **Don't mix schedule types:**
```yaml
days_of_week: ["monday"]
interval_days: 3  # ERROR: Pick one!
```

❌ **Don't mix run start types:**
```yaml
run_1_start_time: "06:00"
run_1_sun_event: "AFTER_SUNRISE"  # ERROR: Pick one!
```

❌ **Don't forget date components:**
```yaml
start_on_year: 2024
start_on_month: 5
# Missing start_on_day!  # ERROR
```

✅ **Do provide complete info:**
```yaml
start_on_year: 2024
start_on_month: 5
start_on_day: 1
```
