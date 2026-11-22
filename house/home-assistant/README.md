# Home Assistant: the hourly price feed

`hourly_price_feed.py` gives HAI's energy weighing the hourly electricity price. It runs inside Home Assistant as a
`command_line` sensor; HAI reads the sensor the `ENERGY_PSP_ENTITY` setting names (its `today` and `tomorrow`
attributes hold the 24 hourly prices).

Copy the script into Home Assistant's `/config` and add the sensor to `configuration.yaml`, with your utility's
prices page and prices API as the two arguments:

```yaml
command_line:
  - sensor:
      name: "Hourly Price Data"
      unique_id: hourly_price_data
      command: "python3 /config/hourly_price_feed.py <prices page URL> <prices API URL>"
      command_timeout: 60
      scan_interval: 1800
      value_template: "{{ value_json.current_price | float(0) | round(5) }}"
      unit_of_measurement: "$/kWh"
      device_class: monetary
      json_attributes:
        - today
        - tomorrow
        - current_hour
```

The script speaks one API shape: it loads the prices page once (the API answers only a session holding the page's
cookie), then POSTs `{"SelectedDate": "Y-M-D"}` to the API for today and tomorrow and reads `hourlyPriceDetails`
(`hour` N covers (N-1):00 to N:00 local time, `price` in $/kWh). A utility with a different API needs its own
`fetch_prices()`; the output the sensor reads stays the same: `current_price`, `current_hour`, `today`, `tomorrow`.
