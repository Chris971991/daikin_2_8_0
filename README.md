# Daikin 2.8.0 Integration for Home Assistant

A comprehensive integration for Daikin air conditioners using local API control.

## Features

- Full climate entity with all HVAC modes and settings
- Additional sensors for temperature, humidity, energy consumption, and runtime
- Status indicators for running state, cooling, and heating
- Proper device registry implementation (all entities appear under one device)
- Works without cloud connectivity or Daikin Cloud Service

## Installation

### HACS Installation (Recommended)

1. Make sure [HACS](https://hacs.xyz) is installed in your Home Assistant instance
2. Add this repository as a custom repository in HACS:
   - Go to HACS → Integrations → ⋮ (menu) → Custom repositories
   - Add the URL of this repository
   - Category: Integration
3. Click "Download" on the Daikin 2.8.0 integration
4. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/daikin_2_8_0` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

Add the following to your `configuration.yaml`:

```yaml
daikin_2_8_0:
  ip_address:
    - "192.168.1.X"  # Replace with your Daikin AC's IP address
  friendly_names:
    "192.168.1.X": "Living Room A/C"  # Optional: customize the name
```

## Available Entities

After setup, your Daikin AC will appear as a device with the following entities:

### Climate Entity
- Full control of your AC (mode, temperature, fan speed, swing)

### Sensors
- Current Temperature
- Outside Temperature
- Humidity
- Energy Usage Today
- Runtime Today
- HVAC Mode
- Fan Mode
- Swing Mode

### Binary Sensors
- Running Status
- Cooling Status
- Heating Status

## Troubleshooting

### Common Issues

- **Integration not showing up**: Verify your IP address is correct and the AC is on the same network
- **Cannot connect to AC**: Make sure your Daikin AC's local API is accessible

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

This integration builds upon the knowledge and work of the Home Assistant community and various Daikin integration projects.
