# Quick Start Guide - Easy UI for Update Program

## 🎯 Goal
Make it easy to configure watering schedules with intuitive UI fields - no complex YAML required!

## 🔧 How It Works

### Old Way (Complex) ❌
```yaml
runs:
  - start_time: "06:00"
    valves:
      - entity_id: switch.valve_1
        duration: 300
      - entity_id: switch.valve_2
        duration: 600
```
*You had to know the exact structure and specify valves per run*

### New Way (Easy) ✅
**Global valve selection + simple run timing:**

```yaml
valves:
  - switch.valve_1
  - switch.valve_2
valve_duration_1: "00:05:00"  # 5 minutes
valve_duration_2: "00:10:00"  # 10 minutes
run_1_start_time: "06:00"
```
*Select valves once, set durations in time format, configure run timing!*

## 📋 Quick Examples

### Example 1: Morning Watering (Equal Time Split)
```yaml
valves: [Front Yard, Back Yard]
total_duration: "00:15:00"      # 15 min total = 7.5 min each
run_1_start_time: "06:00"
days_of_week: [Mon, Wed, Fri]
```

### Example 2: Custom Duration Per Valve
```yaml
valves: [Garden, Lawn, Flowers]  # In order
valve_duration_1: "00:10:00"     # Garden: 10 min
valve_duration_2: "00:15:00"     # Lawn: 15 min
valve_duration_3: "00:05:00"     # Flowers: 5 min
run_1_start_time: "06:00"
interval_days: 2
```

### Example 3: Sunset Watering
```yaml
valves: [Garden]
total_duration: "00:20:00"
run_1_sun_event: "After Sunset"
run_1_sun_offset: 30           # 30 minutes after
interval_days: 2
```

### Example 4: Two Runs Per Day (Same Valves)
```yaml
valves: [Front, Back]
total_duration: "00:12:00"
run_1_start_time: "06:00"
run_2_start_time: "18:00"
days_of_week: [Every day]
```

### Example 5: Concurrent Watering
```yaml
valves: [Zone 1, Zone 2]
total_duration: "00:15:00"
run_1_start_time: "06:00"
run_1_run_concurrently: true   # Both run at once
```

### Example 6: Cycle and Soak (For Slopes)
```yaml
valves: [Hillside Zone]
total_duration: "00:30:00"
run_1_start_time: "05:00"
run_1_cycle_and_soak: true    # Prevents runoff
```

## 🎮 How to Use in Home Assistant

1. Go to **Developer Tools** → **Services**
2. Select service: `rachio_local.update_program`
3. Select your program from the **Program** dropdown
4. Configure valves (global):
   - Select valves in **Valves** field
   - Set **Total Duration** (equal split) OR
   - Set **Valve Duration 1**, **Valve Duration 2**, etc. (custom)
5. Configure runs (up to 3):
   - Set **Run 1 Start Time** OR **Run 1 Sun Event**
   - Optional: Enable **Run Concurrently** or **Cycle and Soak**
   - Repeat for Run 2, Run 3 if needed
6. Choose your schedule type
7. Click **Call Service**

## ⚠️ Important Rules

### ✅ DO:
- Select valves once at the top (used by all runs)
- Use HH:MM:SS format for durations (e.g., "00:15:00")
- Choose EITHER `total_duration` (equal split) OR `valve_duration_X` (custom)
- Choose EITHER fixed time OR sun event per run (not both)
- Select one schedule type only

### ❌ DON'T:
- Don't mix easy fields with the advanced "runs" field
- Don't set both start time AND sun event for the same run
- Don't select multiple schedule types
- Don't forget valve order matters for `valve_duration_X` fields

## 🔍 Field Reference

### Global Valve Configuration:

| Field | Type | Description |
|-------|------|-------------|
| `valves` | Entity List | Select valves (used by all runs) |
| `total_duration` | Time (HH:MM:SS) | Total time split equally across valves |
| `valve_duration_1` | Time (HH:MM:SS) | Duration for 1st valve (optional) |
| `valve_duration_2` | Time (HH:MM:SS) | Duration for 2nd valve (optional) |
| `valve_duration_3` | Time (HH:MM:SS) | Duration for 3rd valve (optional) |
| `valve_duration_4` | Time (HH:MM:SS) | Duration for 4th valve (optional) |

### Per Run (1, 2, or 3):

| Field | Type | Description |
|-------|------|-------------|
| `run_X_start_time` | Time | Fixed time (e.g., 06:00) |
| `run_X_sun_event` | Select | Before/After Sunrise/Sunset |
| `run_X_sun_offset` | Number | Minutes offset from sun event |
| `run_X_run_concurrently` | Boolean | Run all valves simultaneously |
| `run_X_cycle_and_soak` | Boolean | Enable cycle and soak mode |

### Schedule Options (pick ONE):

| Field | Description |
|-------|-------------|
| `days_of_week` | Mon, Tue, Wed, etc. |
| `interval_days` | Every N days |
| `even_days` | Even dates (2nd, 4th, 6th...) |
| `odd_days` | Odd dates (1st, 3rd, 5th...) |

## 💡 Tips

1. **Start Simple**: Configure Run 1 first, test it, then add Run 2 if needed
2. **Check Logs**: If it doesn't work, check Home Assistant logs for error messages
3. **Verify**: Look at program sensor attributes to confirm settings were applied
4. **Sun Events**: Great for adjusting to seasons automatically!

## 🆘 Troubleshooting

**"No valves running"**
- Make sure you selected at least one valve in `run_X_valves`
- Verify you set a start time OR sun event

**"Changes not applying"**
- Check you're using the correct program sensor entity
- Make sure you clicked "Call Service"
- Check Home Assistant logs for errors

**"Invalid configuration"**
- Don't use both easy fields AND advanced `runs` field
- Each run needs EITHER start time OR sun event, not both
- Only use ONE schedule type

## 🚀 Advanced Users

If you need 4+ runs per day or different durations per valve, you can still use the advanced `runs` field with full YAML/JSON control. See `SERVICE_EXAMPLES.md` for details.

---

**Need more help?** Check out:
- `SERVICE_EXAMPLES.md` - Comprehensive examples
- `IMPLEMENTATION_SUMMARY.md` - Technical details
