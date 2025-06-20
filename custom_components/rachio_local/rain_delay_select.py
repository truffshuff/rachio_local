"""Select entity for Rachio rain delay duration."""
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from .const import DOMAIN, DEVICE_TYPE_CONTROLLER

RAIN_DELAY_OPTIONS = [
    (12, "12 hours"),
    (24, "24 hours"),
    (48, "2 days"),
    (72, "3 days"),
    (168, "1 week"),
]

class RachioRainDelayDurationSelect(SelectEntity):
    def __init__(self, handler):
        self._handler = handler
        self._attr_name = f"{handler.name} Rain Delay Duration"
        self._attr_unique_id = f"{handler.device_id}_rain_delay_duration"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_options = [label for _, label in RAIN_DELAY_OPTIONS]
        self._selected_hours = 24
        self._attr_current_option = self._get_label(self._selected_hours)

    def _get_label(self, hours):
        for h, label in RAIN_DELAY_OPTIONS:
            if h == hours:
                return label
        return f"{hours} hours"

    @property
    def current_option(self):
        return self._attr_current_option

    async def async_select_option(self, option: str):
        for hours, label in RAIN_DELAY_OPTIONS:
            if label == option:
                self._selected_hours = hours
                self._attr_current_option = label
                self.async_write_ha_state()
                return

    def get_selected_hours(self):
        return self._selected_hours

def async_setup_entry(hass, config_entry, async_add_entities):
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    for device_id, data in entry_data.items():
        handler = data["handler"]
        if handler.type == DEVICE_TYPE_CONTROLLER:
            entities.append(RachioRainDelayDurationSelect(handler))
    async_add_entities(entities)
