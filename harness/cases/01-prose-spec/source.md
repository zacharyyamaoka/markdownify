# KM80-9 Robot Joint Actuator

The KM80-9 is a quasi-direct-drive actuator for legged robots and collaborative arms. It pairs an outrunner motor of 21 pole pairs with a 9:1 planetary gearbox, so it reaches a rated torque of 9 N·m and a peak torque of 18 N·m at 48 V.

The integrated driver reads a 14-bit magnetic encoder at 40 kHz and closes the current loop at 20 kHz. It talks CAN 2.0B at 1 Mbit/s and accepts position, velocity and torque commands, so one bus can run up to 32 joints.

## Thermal behaviour

Operating temperature is −20 °C to 50 °C ambient. Above 80 °C winding temperature the driver derates the current limit linearly, and at 100 °C it disables the output stage and reports a thermal fault on the bus.

Back-drive torque with the driver off is below 0.4 N·m, which keeps the joint safe to move by hand during set-up.
