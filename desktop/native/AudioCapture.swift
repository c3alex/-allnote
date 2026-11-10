import Foundation
import CoreAudio
import AVFoundation
import CoreMedia
import CoreGraphics

func emit(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object), let line = String(data: data, encoding: .utf8) {
        print(line); fflush(stdout)
    }
}

final class Track {
    let name: String, folder: URL
    var index: Int? = nil
    var samples = [Int16]()
    let rate = 16000, chunk = 80000
    init(_ name: String, _ folder: URL) { self.name = name; self.folder = folder }
    func flush() throws {
        guard let i = index, !samples.isEmpty else { return }
        var data = Data()
        func text(_ value: String) { data.append(value.data(using: .ascii)!) }
        func u32(_ value: UInt32) { var v = value.littleEndian; withUnsafeBytes(of: &v) { data.append(contentsOf: $0) } }
        func u16(_ value: UInt16) { var v = value.littleEndian; withUnsafeBytes(of: &v) { data.append(contentsOf: $0) } }
        text("RIFF"); u32(UInt32(36 + samples.count * 2)); text("WAVEfmt "); u32(16)
        u16(1); u16(1); u32(16000); u32(32000); u16(2); u16(16); text("data"); u32(UInt32(samples.count * 2))
        samples.withUnsafeBytes { data.append(contentsOf: $0) }
        let url = folder.appendingPathComponent(String(format: "%@-%08d.wav", name, i))
        try data.write(to: url, options: .atomic)
        emit(["event":"chunk", "source":name, "path":url.path, "start":Double(i * chunk)/16000.0])
        samples.removeAll(keepingCapacity: true)
    }
    func append(_ input: [Float], at time: Double) throws {
        var position = max(0, Int((time * 16000).rounded()))
        var consumed = 0
        while consumed < input.count {
            let next = position / chunk
            if let old = index, next < old { return }
            if index != next { try flush(); index = next }
            let offset = position % chunk
            if offset > samples.count { samples.append(contentsOf: repeatElement(0, count: offset - samples.count)) }
            let overlap = max(0, samples.count - offset)
            if overlap > 0 {
                let skip = min(overlap, input.count - consumed); position += skip; consumed += skip; continue
            }
            let amount = min(chunk - offset, input.count - consumed)
            for v in input[consumed..<(consumed + amount)] { samples.append(Int16(max(-1, min(1, v)) * 32767)) }
            consumed += amount; position += amount
            if samples.count == chunk { try flush(); index = next + 1 }
        }
    }
}

final class Recorder {
    let queue = DispatchQueue(label: "callnote.capture.disk")
    private var tap: AudioObjectID = 0
    private var device: AudioObjectID = 0
    private var io: AudioDeviceIOProcID?
    private var systemStarted = false
    var engine: AVAudioEngine?
    var tracks: [String:Track] = [:]
    var origin = CMClockGetTime(CMClockGetHostTimeClock()).seconds
    var converters: [String:AVAudioConverter] = [:]
    var formats: [String:AVAudioFormat] = [:]
    var failure = false
    var levelTime: [String:Double] = [:]
    let folder: URL
    init(folder: URL) { self.folder = folder }
    func fail(_ message: String) { if !failure { failure = true; emit(["event":"error", "message":message]) } }
    func consume(_ buffer: AVAudioPCMBuffer, source: String, time: Double) {
        do {
            if formats[source] != buffer.format {
                let output = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate:16000, channels:1, interleaved:false)!
                converters[source] = AVAudioConverter(from:buffer.format, to:output)
                formats[source] = buffer.format
            }
            guard let converter = converters[source] else { throw NSError(domain:"Conversion audio", code:1) }
            let count = AVAudioFrameCount(ceil(Double(buffer.frameLength) * 16000 / buffer.format.sampleRate))
            let result = AVAudioPCMBuffer(pcmFormat:converter.outputFormat, frameCapacity:count)!
            var error: NSError?, used = false
            converter.convert(to:result, error:&error) { _, status in
                if used { status.pointee = .noDataNow; return nil }
                used = true; status.pointee = .haveData; return buffer
            }
            if let error = error { throw error }
            if let data = result.floatChannelData?[0], result.frameLength > 0 {
                let values = Array(UnsafeBufferPointer(start:data, count:Int(result.frameLength)))
                if time - (levelTime[source] ?? -1) >= 0.5 {
                    emit(["event":"level","source":source,"peak":values.map{abs($0)}.max() ?? 0]); levelTime[source] = time
                }
                if tracks[source] == nil { tracks[source] = Track(source,folder) }
                try tracks[source]!.append(values, at:max(0,time-origin))
            }
        } catch { fail(error.localizedDescription) }
    }
    private func check(_ status: OSStatus, _ operation: String) throws {
        guard status == noErr else {
            throw NSError(domain:"Callnote.CoreAudio", code:Int(status), userInfo:[
                NSLocalizedDescriptionKey:"Системный звук: \(operation) (Core Audio \(status))."
            ])
        }
    }

    private func startSystemAudio() throws {
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.name = "Callnote System Audio"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        try check(AudioHardwareCreateProcessTap(description, &tap), "создание источника")
        var address = AudioObjectPropertyAddress(mSelector:kAudioTapPropertyFormat,
            mScope:kAudioObjectPropertyScopeGlobal, mElement:kAudioObjectPropertyElementMain)
        var asbd = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        try check(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &asbd), "чтение формата")
        guard let format = AVAudioFormat(streamDescription:&asbd), asbd.mBytesPerFrame > 0 else {
            throw NSError(domain:"Callnote.CoreAudio", code:1, userInfo:[NSLocalizedDescriptionKey:"Формат системного звука не поддерживается."])
        }
        let properties: [String:Any] = [
            kAudioAggregateDeviceNameKey: "Callnote Capture",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceTapListKey: [[
                kAudioSubTapUIDKey: description.uuid.uuidString,
                kAudioSubTapDriftCompensationKey: true
            ]]
        ]
        try check(AudioHardwareCreateAggregateDevice(properties as CFDictionary, &device), "создание устройства захвата")
        let bytesPerFrame = Int(asbd.mBytesPerFrame)
        try check(AudioDeviceCreateIOProcIDWithBlock(&io, device, queue) { [weak self] _, input, timestamp, _, _ in
            let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating:input))
            guard let first = buffers.first, first.mDataByteSize > 0,
                  let pcm = AVAudioPCMBuffer(pcmFormat:format, bufferListNoCopy:input) else { return }
            pcm.frameLength = AVAudioFrameCount(Int(first.mDataByteSize) / bytesPerFrame)
            let time = AVAudioTime.seconds(forHostTime:timestamp.pointee.mHostTime)
            self?.consume(pcm, source:"system", time:time)
        }, "подключение входа")
        try check(AudioDeviceStart(device, io), "запуск устройства захвата")
        systemStarted = true
    }

    func start(mode: String) async throws {
        if mode != "system" {
            let granted = await AVCaptureDevice.requestAccess(for:.audio)
            if !granted { throw NSError(domain:"Callnote.AudioCapture", code:1, userInfo:[NSLocalizedDescriptionKey:"Нет доступа к микрофону. Разрешите Callnote доступ в настройках macOS."]) }
        }
        origin = CMClockGetTime(CMClockGetHostTimeClock()).seconds
        emit(["event":"started", "date":ISO8601DateFormatter().string(from:Date()), "mode":mode])
        if mode != "mic" { try startSystemAudio() }
        if mode != "system" {
            let engine = AVAudioEngine(); self.engine = engine
            let input = engine.inputNode
            input.installTap(onBus:0, bufferSize:4096, format:input.outputFormat(forBus:0)) { buffer,time in
                // Copy the buffer before the audio callback returns.
                guard let copy = AVAudioPCMBuffer(pcmFormat:buffer.format, frameCapacity:buffer.frameLength) else { return }
                copy.frameLength = buffer.frameLength
                let src = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
                let dst = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
                for i in 0..<src.count { if let a=src[i].mData, let b=dst[i].mData { memcpy(b,a,Int(src[i].mDataByteSize)) } }
                let seconds = AVAudioTime.seconds(forHostTime:time.hostTime)
                self.queue.async { self.consume(copy, source:"microphone", time:seconds) }
            }
            try engine.start()
        }
        emit(["event":"ready"])
    }
    func stop() async {
        if let engine = engine { engine.inputNode.removeTap(onBus:0); engine.stop() }
        if systemStarted { AudioDeviceStop(device, io) }
        if let io = io { AudioDeviceDestroyIOProcID(device, io) }
        if device != 0 { AudioHardwareDestroyAggregateDevice(device) }
        if tap != 0 { AudioHardwareDestroyProcessTap(tap) }
        queue.sync {
            for track in tracks.values { do { try track.flush() } catch { fail(error.localizedDescription) } }
        }
        emit(["event":"stopped"])
    }
}

@main struct CaptureMain {
    static func main() async {
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--self-test" {
            guard #available(macOS 13.0,*) else { exit(1) }
            let folder = URL(fileURLWithPath:CommandLine.arguments[2],isDirectory:true)
            do {
                try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
                let recorder = Recorder(folder:folder)
                let format = AVAudioFormat(commonFormat:.pcmFormatFloat32,sampleRate:48000,channels:2,interleaved:false)!
                let buffer = AVAudioPCMBuffer(pcmFormat:format,frameCapacity:48000)!
                buffer.frameLength = 48000
                for c in 0..<2 { for i in 0..<48000 { buffer.floatChannelData![c][i] = sin(Float(i)*0.03)*0.2 } }
                recorder.consume(buffer,source:"system",time:recorder.origin)
                recorder.consume(buffer,source:"microphone",time:recorder.origin)
                recorder.consume(buffer,source:"system",time:recorder.origin+5)
                for track in recorder.tracks.values { try track.flush() }
                if recorder.failure { exit(1) }
                emit(["event":"self-test","ok":true]); return
            } catch { emit(["event":"error","message":error.localizedDescription]); exit(1) }
        }
        if CommandLine.arguments.contains("--check") {
            emit(["event":"permissions", "screen":CGPreflightScreenCaptureAccess(),
                  "microphone":AVCaptureDevice.authorizationStatus(for:.audio).rawValue]); return
        }
        guard #available(macOS 13.0,*), CommandLine.arguments.count == 3 else {
            emit(["event":"error","message":"Требуется macOS 13+; AudioCapture <folder> <mic|system|both>"]); exit(1)
        }
        let mode = CommandLine.arguments[2]
        guard ["mic","system","both"].contains(mode) else { exit(1) }
        let folder = URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
        let recorder = Recorder(folder:folder)
        do {
            try FileManager.default.createDirectory(at:folder, withIntermediateDirectories:true)
            try await recorder.start(mode:mode)
            await withCheckedContinuation { (continuation:CheckedContinuation<Void,Never>) in
                DispatchQueue.global().async { _ = readLine(); continuation.resume() }
            }
            await recorder.stop()
            if recorder.failure { exit(1) }
        } catch { emit(["event":"error","message":error.localizedDescription]); await recorder.stop(); exit(1) }
    }
}
