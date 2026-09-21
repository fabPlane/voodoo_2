#!/usr/bin/env swift

import Foundation
import IOKit.hid

private func property(_ device: IOHIDDevice, _ key: String) -> Any? {
    IOHIDDeviceGetProperty(device, key as CFString)
}

private func stringProperty(_ device: IOHIDDevice, _ key: String) -> String {
    property(device, key) as? String ?? "?"
}

private func intProperty(_ device: IOHIDDevice, _ key: String) -> Int {
    if let n = property(device, key) as? NSNumber { return n.intValue }
    return 0
}

private func registryID(_ device: IOHIDDevice) -> UInt64 {
    var value: UInt64 = 0
    IORegistryEntryGetRegistryEntryID(IOHIDDeviceGetService(device), &value)
    return value
}

private func isKeyboard(_ device: IOHIDDevice) -> Bool {
    intProperty(device, kIOHIDPrimaryUsagePageKey) == kHIDPage_GenericDesktop &&
        intProperty(device, kIOHIDPrimaryUsageKey) == kHIDUsage_GD_Keyboard
}

private func describesTarget(_ device: IOHIDDevice) -> Bool {
    let text = [
        stringProperty(device, kIOHIDProductKey),
        stringProperty(device, kIOHIDManufacturerKey)
    ].joined(separator: " ").lowercased()
    let vendor = intProperty(device, kIOHIDVendorIDKey)
    return vendor == 0x359f || text.contains("voodoo") || text.contains("sipeed") || text.contains("tang")
}

private let plainKeys: [Int: Character] = [
    0x04:"a", 0x05:"b", 0x06:"c", 0x07:"d", 0x08:"e", 0x09:"f", 0x0a:"g", 0x0b:"h",
    0x0c:"i", 0x0d:"j", 0x0e:"k", 0x0f:"l", 0x10:"m", 0x11:"n", 0x12:"o", 0x13:"p",
    0x14:"q", 0x15:"r", 0x16:"s", 0x17:"t", 0x18:"u", 0x19:"v", 0x1a:"w", 0x1b:"x",
    0x1c:"y", 0x1d:"z", 0x1e:"1", 0x1f:"2", 0x20:"3", 0x21:"4", 0x22:"5", 0x23:"6",
    0x24:"7", 0x25:"8", 0x26:"9", 0x27:"0", 0x28:"\n", 0x2b:"\t", 0x2c:" ",
    0x2d:"-", 0x2e:"=", 0x2f:"[", 0x30:"]", 0x31:"\\", 0x33:";", 0x34:"'",
    0x35:"`", 0x36:",", 0x37:".", 0x38:"/"
]

private let shiftedKeys: [Int: Character] = [
    0x1e:"!", 0x1f:"@", 0x20:"#", 0x21:"$", 0x22:"%", 0x23:"^", 0x24:"&", 0x25:"*",
    0x26:"(", 0x27:")", 0x2d:"_", 0x2e:"+", 0x2f:"{", 0x30:"}", 0x31:"|",
    0x33:":", 0x34:"\"", 0x35:"~", 0x36:"<", 0x37:">", 0x38:"?"
]

private final class WatchState {
    var baseline = Set<UInt64>()
    var watched = Set<UInt64>()
    var shiftDown = Set<Int>()
    var serialNodes = Set<String>()

    func describe(_ device: IOHIDDevice) -> String {
        let product = stringProperty(device, kIOHIDProductKey)
        let maker = stringProperty(device, kIOHIDManufacturerKey)
        let vid = intProperty(device, kIOHIDVendorIDKey)
        let pid = intProperty(device, kIOHIDProductIDKey)
        return "\(maker) \(product) [\(String(format: "%04x:%04x", vid, pid))]"
    }

    func scanSerialNodes() {
        let directory = (try? FileManager.default.contentsOfDirectory(atPath: "/dev")) ?? []
        let current = Set(directory.filter {
            $0.hasPrefix("cu.usbmodem") || $0.hasPrefix("cu.usbserial")
        }.map { "/dev/\($0)" })
        for node in current.subtracting(serialNodes).sorted() {
            print("\n[SERIAL ATTACHED] \(node)")
        }
        for node in serialNodes.subtracting(current).sorted() {
            print("\n[SERIAL REMOVED]  \(node)")
        }
        serialNodes = current
        fflush(stdout)
    }

    func deviceAppeared(_ device: IOHIDDevice) {
        let id = registryID(device)
        guard isKeyboard(device) else { return }
        let newDevice = !baseline.contains(id)
        guard newDevice || describesTarget(device) else { return }
        watched.insert(id)
        print("\n[HID KEYBOARD ATTACHED] \(describe(device))")
        print("[HID TEXT] ", terminator: "")
        fflush(stdout)
    }

    func deviceRemoved(_ device: IOHIDDevice) {
        let id = registryID(device)
        if watched.remove(id) != nil {
            print("\n[HID KEYBOARD REMOVED] \(describe(device))")
            fflush(stdout)
        }
    }

    func valueChanged(_ value: IOHIDValue) {
        let element = IOHIDValueGetElement(value)
        let device = IOHIDElementGetDevice(element)
        guard watched.contains(registryID(device)) else { return }
        let page = Int(IOHIDElementGetUsagePage(element))
        let usage = Int(IOHIDElementGetUsage(element))
        let pressed = IOHIDValueGetIntegerValue(value) != 0
        guard page == kHIDPage_KeyboardOrKeypad else { return }

        if usage == 0xe1 || usage == 0xe5 {
            if pressed { shiftDown.insert(usage) } else { shiftDown.remove(usage) }
            return
        }
        guard pressed else { return }
        var character = shiftDown.isEmpty ? plainKeys[usage] : shiftedKeys[usage]
        if character == nil, !shiftDown.isEmpty, let lower = plainKeys[usage], lower.isLetter {
            character = Character(String(lower).uppercased())
        }
        if let character {
            print(String(character), terminator: "")
        } else {
            print("<key:\(String(format: "%02x", usage))>", terminator: "")
        }
        fflush(stdout)
    }
}

private let state = WatchState()
private let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
IOHIDManagerSetDeviceMatching(manager, nil)
IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))

if let devices = IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice> {
    state.baseline = Set(devices.map(registryID))
}
state.scanSerialNodes()

IOHIDManagerRegisterDeviceMatchingCallback(manager, { context, _, _, device in
    Unmanaged<WatchState>.fromOpaque(context!).takeUnretainedValue().deviceAppeared(device)
}, Unmanaged.passUnretained(state).toOpaque())

IOHIDManagerRegisterDeviceRemovalCallback(manager, { context, _, _, device in
    Unmanaged<WatchState>.fromOpaque(context!).takeUnretainedValue().deviceRemoved(device)
}, Unmanaged.passUnretained(state).toOpaque())

IOHIDManagerRegisterInputValueCallback(manager, { context, _, _, value in
    Unmanaged<WatchState>.fromOpaque(context!).takeUnretainedValue().valueChanged(value)
}, Unmanaged.passUnretained(state).toOpaque())

IOHIDManagerScheduleWithRunLoop(manager, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in state.scanSerialNodes() }

print("Watching for a newly attached USB HID keyboard and USB serial ports.")
print("Existing keyboards are ignored. Press Control-C to stop.\n")
fflush(stdout)
RunLoop.current.run()
