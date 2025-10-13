# 🚀 Rachio Local
**Rachio Local** Plugin for Home Assistant is a lightweight integration that enables seamless control and monitoring of Rachio irrigation systems through the Rachio API. By leveraging secure, outgoing API calls, the plugin allows Home Assistant users to retrieve real-time device status, manage watering schedules, control irrigation zones, and receive updates about their Rachio sprinkler system. Unlike traditional integrations that require complex webhook setups, this plugin simplifies connectivity by making direct, authenticated API requests, ensuring a straightforward and secure method of interfacing with Rachio's cloud services without exposing your home network to inbound connections.

---
<p align="center">
  <img src="images/rachio-local.png" alt="Rachio Local" width="400"/>
</p>

---
## 💸 Donations Appreciated!
If you find this plugin useful, please consider donating. Your support is greatly appreciated!

[![Sponsor Me](https://img.shields.io/badge/Sponsor%20Me-%F0%9F%92%AA-purple?style=for-the-badge)](https://github.com/sponsors/biofects?frequency=recurring&sponsor=biofects)

[![paypal](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=TWRQVYJWC77E6)
---

## 🔍 About this Plugin
 **Rachio Local** removes the need to open your home assistant to public network for incoming traffic. Rachio allows 1700 calls a day to API and I have done my best to prevent exhausting them
 Let me break down the API calls under different scenarios:

**Normal Operations (No Active Watering)**
```
Base polling rate: Every 5 minutes
Daily calls = (24 hours × 60 minutes) ÷ 5 minutes = 288 base calls
```
**During Active Watering**
```
Polling increases to every 30 seconds while any zone or schedule is running
Example: For a 2-hour watering schedule
Additional calls = (2 hours × 60 minutes × 2 calls per minute) = 240 calls during that period
```
**Typical Day Example**
```
Base calls: 288
If you run 4 schedules per day:
Each schedule might run for ~2 hours
(4 schedules × 2 hours × 120 calls/hour) = ~960 calls during active watering
The rest of the day uses the 5-minute interval
```
**Estimated Total**
```
Normal day with 4 schedule runs: ~800-1000 API calls
Days with additional manual zone control: Add ~120 calls per hour of manual watering
This is significantly less than your current 1700 calls per day. The reduction comes from:
Using 5-minute intervals during inactive periods
Only using 30-second polling during active watering
```
**Efficient state management with pending operations**
```
Smart handling of schedule and zone transitions
The exact number will vary based on:
How many schedules run each day
Duration of each schedule
How often you manually control zones
Whether schedules overlap
```
---
## Features
- Zone Watering Control: Ability to start and stop individual irrigation zones through switches in Home Assistant.
- Device and Zone Status Sensors: Sensors that provide real-time status of the Rachio device and individual zones, including current watering state.
- Schedule Management: Switches to control and monitor irrigation schedules, allowing start and stop of predefined watering schedules.
- Last Watered Timestamp: Sensors that track and display the last time each zone was watered.
- Periodic Data Polling: Automatic data updates every 5 minutes to keep Home Assistant synchronized with the Rachio device status.
- **Dynamic Polling & Diagnostics:** Polling interval, device count, and API call usage are now exposed as Home Assistant sensors for full transparency and troubleshooting.
- **Robust State Sync:** Zone and schedule sensors always reflect the true running state, even for manual runs started from the Rachio app.
- **Multi-Device Support:** Improved logic for setups with multiple controllers and/or smart hose timers.
- **Enhanced Logging:** Debug logs for all state transitions and API responses for easier troubleshooting.
- **Smart Hose Timer Program Management:** Services to enable, disable, and update Smart Hose Timer programs directly from Home Assistant.

## 🔧 Smart Hose Timer Program Management Services

The integration now provides three powerful services for managing Smart Hose Timer programs:

### 1. Enable Program (`rachio_local.enable_program`)

Enable a disabled Smart Hose Timer program.

**Parameters:**
- `program_id` (required): Select a program sensor entity (e.g., `sensor.smart_hose_timer_program_morning_watering`)

**Example YAML:**
```yaml
service: rachio_local.enable_program
data:
  program_id: sensor.smart_hose_timer_program_morning_watering
```

### 2. Disable Program (`rachio_local.disable_program`)

Disable an active Smart Hose Timer program.

**Parameters:**
- `program_id` (required): Select a program sensor entity (e.g., `sensor.smart_hose_timer_program_morning_watering`)

**Example YAML:**
```yaml
service: rachio_local.disable_program
data:
  program_id: sensor.smart_hose_timer_program_evening_watering
```

### 3. Update Program (`rachio_local.update_program`)

Update multiple settings of a Smart Hose Timer program at once. You only need to include the settings you want to change.

**⚠️ Important:** Only **ONE** scheduling type can be used per program: `days_of_week` **OR** `interval_days` **OR** `even_days` **OR** `odd_days`. Specifying multiple scheduling types will result in an error.

**⚠️ Important:** Only **ONE** start time type can be used: `start_time` (fixed) **OR** `sun_event` + `sun_offset_minutes` (sun-based). Specifying both will result in an error.

**Parameters:**
- `program_id` (required): Select a program sensor entity
- `enabled` (optional): Enable or disable the program (boolean)
- `name` (optional): Change the program name (text)
- `rain_skip_enabled` (optional): Enable or disable rain skip (boolean)
- `color` (optional): Set the program color in hex format (e.g., `#00A7E1`)
- `start_time` (optional): Fixed program start time in HH:MM format, 24-hour (e.g., `06:30`) - Cannot be used with `sun_event`
- `sun_event` (optional): Start relative to sunrise/sunset (`BEFORE_RISE`, `AFTER_RISE`, `BEFORE_SET`, `AFTER_SET`) - Cannot be used with `start_time`
- `sun_offset_minutes` (optional): Minutes offset from sun event (use with `sun_event`). Positive for after, negative for before (e.g., `30` = 30 minutes after, `-15` = 15 minutes before)
- `days_of_week` (optional): Select specific days for weekly schedules (list: `monday`, `tuesday`, `wednesday`, `thursday`, `friday`, `saturday`, `sunday`)
- `interval_days` (optional): Set interval between runs in days (number: 1-30)
- `even_days` (optional): Run on even calendar days (boolean)
- `odd_days` (optional): Run on odd calendar days (boolean)
- `valves` (optional): Configure which valves to run and their durations (list of objects with `entity_id` and `duration` in seconds)

**Example YAML - Change name and enable rain skip:**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_morning_watering
  name: "Front Yard Morning"
  rain_skip_enabled: true
```

**Example YAML - Set weekly schedule (Mon/Wed/Fri at 6:30 AM):**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_morning_watering
  start_time: "06:30"
  days_of_week:
    - monday
    - wednesday
    - friday
```

**Example YAML - Set interval schedule (every 3 days):**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_lawn_watering
  interval_days: 3
  start_time: "07:00"
```

**Example YAML - Disable program and change color:**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_evening_watering
  enabled: false
  color: "#FF5733"
```

**Example YAML - Set odd days schedule:**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_garden
  odd_days: true
  runs:
    - start_time: "18:00"
      valves:
        - entity_id: switch.backyard_valve
          duration: 600
```

**Example YAML - Multiple runs per day (morning fixed time + evening sun-based):**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_lawn
  days_of_week:
    - monday
    - wednesday
    - friday
  runs:
    - start_time: "06:00:00"
      valves:
        - entity_id: switch.front_lawn_valve
          duration: 600  # 10 minutes at 6 AM
    - sun_event: "AFTER_SET"
      sun_offset_minutes: 30
      valves:
        - entity_id: switch.back_lawn_valve
          duration: 300  # 5 minutes 30 min after sunset
```

**Example YAML - Multiple runs with different valve combinations:**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_garden
  interval_days: 2
  runs:
    - start_time: "07:00:00"
      valves:
        - entity_id: switch.vegetable_garden_valve
          duration: 900  # 15 minutes morning watering
    - start_time: "19:00:00"
      valves:
        - entity_id: switch.flower_bed_valve
          duration: 300  # 5 minutes evening watering
        - entity_id: switch.shrubs_valve
          duration: 450  # 7.5 minutes evening watering
```

**Example YAML - Single run with multiple valves:**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_front_yard
  days_of_week:
    - tuesday
    - thursday
    - saturday
  runs:
    - start_time: "06:00:00"
      valves:
        - entity_id: switch.front_lawn_valve
          duration: 600  # 10 minutes
        - entity_id: switch.front_garden_valve
          duration: 300  # 5 minutes
        - entity_id: switch.front_shrubs_valve
          duration: 450  # 7.5 minutes
```

**Example YAML - Sun-based run (30 minutes before sunrise):**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_morning_watering
  days_of_week:
    - monday
    - wednesday
    - friday
  runs:
    - sun_event: "BEFORE_RISE"
      sun_offset_minutes: -30  # 30 minutes before sunrise
      valves:
        - entity_id: switch.lawn_valve
          duration: 900
```

**Example YAML - Sun-based run (1 hour after sunset):**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_evening_watering
  interval_days: 2
  runs:
    - sun_event: "AFTER_SET"
      sun_offset_minutes: 60  # 1 hour after sunset
      valves:
        - entity_id: switch.garden_valve
          duration: 600
```

**Example YAML - Complex multi-run program:**
```yaml
service: rachio_local.update_program
data:
  program_id: sensor.smart_hose_timer_program_complete_yard
  name: "Complete Yard Watering"
  color: "#4CAF50"
  rain_skip: true
  days_of_week:
    - monday
    - wednesday
    - friday
    - sunday
  runs:
    # Morning: Front yard at sunrise
    - sun_event: "AFTER_RISE"
      sun_offset_minutes: 15
      valves:
        - entity_id: switch.front_lawn_valve
          duration: 600
        - entity_id: switch.front_garden_valve
          duration: 420
    # Midday: Vegetables at fixed time
    - start_time: "12:00:00"
      valves:
        - entity_id: switch.vegetable_garden_valve
          duration: 300
    # Evening: Back yard after sunset
    - sun_event: "AFTER_SET"
      sun_offset_minutes: 30
      valves:
        - entity_id: switch.back_lawn_valve
          duration: 900
        - entity_id: switch.back_garden_valve
          duration: 600
        - entity_id: switch.shrubs_valve
          duration: 450
```

### Using Services in Developer Tools

1. Go to **Developer Tools** → **Services** in Home Assistant
2. Select one of the Rachio Local services from the dropdown
3. Use the UI to select your program entity from the dropdown
4. Fill in any optional fields as needed
5. Click **Call Service**

The program entities will automatically update to reflect the changes after the service is called.

### API Efficiency

These services use the minimal API payload required by Rachio's `updateProgramV2` endpoint. You only need to send the program ID and the fields you want to change, making efficient use of your daily API call limit.

**API Calls Per Service Invocation:**
- 1 call to `updateProgramV2` (to update the program)
- 1 call to `getProgramV2` (to fetch fresh program details)
- **Total: 2 API calls** (no full device refresh triggered)

The services use `async_set_updated_data()` to notify entities of the change without triggering a full coordinator refresh, minimizing API usage while keeping your UI instantly updated.

## 🚨 Changelog

See [CHANGELOG.md](./CHANGELOG.md) for the full changelog.

## Notes
My Home Assistant runs in Docker on a server. I don't use the supervised version, nor do I want to expose Home Assistant servers publicly. This is my personal choice, despite it requiring more manual management.
  
  <strong style="color:red;">I don’t know how it works with HAOS.</strong>

- I prioritize securing my network by blocking **phone home** calls from IoT devices, ensuring data privacy, or allowing thrid party webhooks.
- My Home Assistant setup includes an array of devices, with plans to expand further. Here’s a snapshot of what I manage:

    - 💡 Hue
    - 🏠 Tuya
    - 📊 Grafana for monitoring
    - 🎞 Emby for media
    - 🚪 Door locks
    - 🔒 Cameras
    - 💻 Local AI (on server)
    - 🚦 Pi Hole for ad-blocking
    - 🖥 Full network & server monitoring, including GPU & storage
    - 🌀 Fans
    - And much more to come!



## 🚀 Installation Instructions

### HACS (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed in your Home Assistant.
2. Open the HACS panel, click the three dots in the top-right corner, and select **"Custom Repositories."**
3. Add the following URL as a **Custom Repository**:  
   [https://github.com/biofects/rachio_local](https://github.com/biofects/rachio_local/)  
   and select **"Integration"** as the category.
5. Click **"Add,"** then navigate to the **"Integration"** tab, click **"+ Explore & Download Repositories"** and search for "Rachio"
6. Install the plugin and restart Home Assistant.
7. Go to **settings Device and Integrations** in Home Assistant and add plugin Rachio Local.
8. You will need to get your API Key from https://app.rach.io/
9. Enter you API Key
---
## Manual installation
1. Create plugin Folder 
    ```
    # Create rachio_local Folder
    mkdir -p /config/custom_components/rachio_local
    ```
2. Copy the files for plugin into your Home Assistant plugin folder
    ```
    
    cp config_flow.py  const.py  __init__.py  manifest.json  sensor.py  switch.py /config/custom_components/rachio_local
    ```

4. Restart Home Assistant


---

## � Contributors

Special thanks to the amazing contributors who have helped improve this project:

- **[@truffshuff](https://github.com/truffshuff)** - Major Smart Hose Timer enhancements, improved state tracking, persistent last watered times, enhanced diagnostics, and API optimization improvements. 🎉

We appreciate all contributions, bug reports, and feature requests from the community!

---

## �🐛 Support & Issues
If you encounter bugs or have feature requests, feel free to [open an issue](https://github.com/biofects/rachio_local/issues) on the GitHub repository.

---

## 📜 License
This project is licensed under the MIT License.
