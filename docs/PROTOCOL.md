# iCUE Link Hub Protocol

This document summarizes how `FanControl.CorsairLink` communicates with the iCUE Link hub to read fan/pump speeds and temperatures. It is based on the code in the repository.

## Command, Endpoint and Data Type Codes

`ICueLinkHubDevice` defines the constants for commands, endpoints and data types used when communicating with the hub:

```csharp
    private static class Commands
    {
        private const byte HANDLE_ID = 0x01;

        public static ReadOnlySpan<byte> EnterSoftwareMode => new byte[] { 0x01, 0x03, 0x00, 0x02 };
        public static ReadOnlySpan<byte> EnterHardwareMode => new byte[] { 0x01, 0x03, 0x00, 0x01 };
        public static ReadOnlySpan<byte> ReadFirmwareVersion => new byte[] { 0x02, 0x13 };
        public static ReadOnlySpan<byte> OpenEndpoint => new byte[] { 0x0d, HANDLE_ID };
        public static ReadOnlySpan<byte> CloseEndpoint => new byte[] { 0x05, 0x01, HANDLE_ID };
        public static ReadOnlySpan<byte> Read => new byte[] { 0x08, HANDLE_ID };
        public static ReadOnlySpan<byte> Write => new byte[] { 0x06, HANDLE_ID };
    }

    private static class Endpoints
    {
        public static ReadOnlySpan<byte> GetSpeeds => new byte[] { 0x17 };
        public static ReadOnlySpan<byte> GetTemperatures => new byte[] { 0x21 };
        public static ReadOnlySpan<byte> SoftwareSpeedFixedPercent => new byte[] { 0x18 };
        public static ReadOnlySpan<byte> GetSubDevices => new byte[] { 0x36 };
    }

    private static class DataTypes
    {
        public static ReadOnlySpan<byte> Speeds => new byte[] { 0x25, 0x00 };
        public static ReadOnlySpan<byte> Temperatures => new byte[] { 0x10, 0x00 };
        public static ReadOnlySpan<byte> SoftwareSpeedFixedPercent => new byte[] { 0x07, 0x00 };
        public static ReadOnlySpan<byte> SubDevices => new byte[] { 0x21, 0x00 };
        public static ReadOnlySpan<byte> Continuation => new byte[] { };
```
```

## HID Packet Format

`LinkHubDataWriter.CreateCommandPacket` shows how each command is wrapped before being sent to the HID device:

```csharp
    public static byte[] CreateCommandPacket(int bufferSize, ReadOnlySpan<byte> command, ReadOnlySpan<byte> data)
    {
        const int HEADER_LENGTH = 3;

        // [0] = 0x00
        // [1] = 0x00
        // [2] = 0x01
        // [3,a] = command
        // [a+1,] = data

        var writeBuf = new byte[bufferSize];
        writeBuf[2] = 0x01;

        var commandSpan = writeBuf.AsSpan(HEADER_LENGTH, command.Length);
        command.CopyTo(commandSpan);

        if (data.Length > 0)
        {
            var dataSpan = writeBuf.AsSpan(HEADER_LENGTH + commandSpan.Length, data.Length);
            data.CopyTo(dataSpan);
        }

        return writeBuf;
    }
```

A full HID report of 513 bytes is written (`PACKET_SIZE_OUT`) and a 512 byte response is read (`PACKET_SIZE`).

## Reading Sensor Data

`ReadFromEndpoint` demonstrates how the driver queries an endpoint and waits for a specific data type:

```csharp
    private EndpointResponse ReadFromEndpoint(ReadOnlySpan<byte> endpoint, ReadOnlySpan<byte> dataType)
    {
        byte[] res;

        using (_guardManager.AwaitExclusiveAccess())
        {
            SendCommand(Commands.CloseEndpoint, endpoint);
            SendCommand(Commands.OpenEndpoint, endpoint);
            res = SendCommand(Commands.Read, waitForDataType: dataType);
            SendCommand(Commands.CloseEndpoint, endpoint);
        }

        return new EndpointResponse(res, dataType);
    }
```

The payload returned by `EndpointResponse.GetData()` skips the HID report ID, the error code and the two byte data type:

```csharp
        public byte[] Payload { get; }
        public byte[] DataType { get; }

        public ReadOnlySpan<byte> GetData() => Payload.AsSpan().Slice(4 + DataType.Length);
    }
```

`LinkHubDataReader` then parses the speed and temperature packets:

```csharp

    public static IReadOnlyCollection<LinkHubSpeedSensor> GetSpeedSensors(ReadOnlySpan<byte> packet)
    {
        var count = packet[6];
        var sensorData = packet.Slice(7);
        var sensors = new List<LinkHubSpeedSensor>(count);

        for (int i = 0, s = 0; i < count; i++, s += 3)
        {
            var currentSensor = sensorData.Slice(s, 3);
            var status = (LinkHubSpeedSensorStatus)currentSensor[0];
            int? rpm = status == LinkHubSpeedSensorStatus.Available
                ? BinaryPrimitives.ReadInt16LittleEndian(currentSensor.Slice(1, 2))
                : null;

            sensors.Add(new LinkHubSpeedSensor(i, status, rpm));
        }

        return sensors;
    }

    public static IReadOnlyCollection<LinkHubTemperatureSensor> GetTemperatureSensors(ReadOnlySpan<byte> packet)
    {
        var count = packet[6];
        var sensorData = packet.Slice(7);
        var sensors = new List<LinkHubTemperatureSensor>(count);

        for (int i = 0, s = 0; i < count; i++, s += 3)
        {
            var currentSensor = sensorData.Slice(s, 3);
            var status = (LinkHubTemperatureSensorStatus)currentSensor[0];
            float? tempCelsius = status == LinkHubTemperatureSensorStatus.Available
                ? BinaryPrimitives.ReadInt16LittleEndian(currentSensor.Slice(1, 2)) / 10f
                : null;

            sensors.Add(new LinkHubTemperatureSensor(i, status, tempCelsius));
```

Each packet begins with a sensor count at byte 6 followed by three bytes per sensor:

* Byte 0 – status (0x00 = available)
* Byte 1‑2 – little‑endian value (RPM or temperature ×10°C)

