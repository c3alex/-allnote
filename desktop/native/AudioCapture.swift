import Foundation
import ScreenCaptureKit
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

@available(macOS 13.0, *)
final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let queue = DispatchQueue(label: "callnote.capture.disk")
    var stream: SCStream?
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
    func stream(_ stream: SCStream, didStopWithError error: Error) { queue.async { self.fail(error.localizedDescription) } }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid,
              let desc = sampleBuffer.formatDescription else { return }
        let format = AVAudioFormat(cmAudioFormatDescription:desc)
        let size = MemoryLayout<AudioBufferList>.size + Int(format.channelCount) * MemoryLayout<AudioBuffer>.size
        let pointer = UnsafeMutableRawPointer.allocate(byteCount:size, alignment:MemoryLayout<AudioBufferList>.alignment)
        defer { pointer.deallocate() }
        let list = pointer.bindMemory(to:AudioBufferList.self, capacity:1)
        var block: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(sampleBuffer, bufferListSizeNeededOut:nil,
            bufferListOut:list, bufferListSize:size, blockBufferAllocator:kCFAllocatorDefault,
            blockBufferMemoryAllocator:kCFAllocatorDefault, flags:0, blockBufferOut:&block)
        guard status == noErr, let pcm = AVAudioPCMBuffer(pcmFormat:format, bufferListNoCopy:list) else { return }
        pcm.frameLength = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        consume(pcm, source:"system", time:sampleBuffer.presentationTimeStamp.seconds)
    }
    func start(mode: String) async throws {
        if mode != "system" {
            let granted = await AVCaptureDevice.requestAccess(for:.audio)
            if !granted { throw NSError(domain:"Callnote.AudioCapture", code:1, userInfo:[NSLocalizedDescriptionKey:"Нет доступа к микрофону. Разрешите Callnote доступ в настройках macOS."]) }
        }
        if mode != "mic" {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly:true)
            guard let display = content.displays.first else { throw NSError(domain:"Экран не найден",code:1) }
            let excluded = content.applications.filter { $0.bundleIdentifier == "com.callnote.desktop" }
            let filter = SCContentFilter(display:display, excludingApplications:excluded, exceptingWindows:[])
            let config = SCStreamConfiguration()
            config.capturesAudio = true; config.excludesCurrentProcessAudio = true
            config.sampleRate = 48000; config.channelCount = 2
            config.width = 2; config.height = 2; config.minimumFrameInterval = CMTime(value:1,timescale:1)
            let capture = SCStream(filter:filter, configuration:config, delegate:self)
            try capture.addStreamOutput(self, type:.audio, sampleHandlerQueue:queue)
            self.stream = capture
        }
        origin = CMClockGetTime(CMClockGetHostTimeClock()).seconds
        emit(["event":"started", "date":ISO8601DateFormatter().string(from:Date()), "mode":mode])
        if let stream = stream { try await stream.startCapture() }
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
        try? await stream?.stopCapture()
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
