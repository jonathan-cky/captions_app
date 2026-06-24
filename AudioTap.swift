import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreAudio

// ── Constants ────────────────────────────────────────────────────────────────
let kSampleRate: Double = 16000   // Whisper expects 16 kHz
let kChannels:   Int    = 1       // mono

// ── Main async entry point ───────────────────────────────────────────────────
final class AudioTap: NSObject {

    private var stream: SCStream?
    private let queue = DispatchQueue(label: "audio.tap.queue", qos: .userInteractive)
    private var converter: AVAudioConverter?
    private var inputFormat:  AVAudioFormat?
    private var outputFormat: AVAudioFormat?

    // stdout handle — write raw PCM here
    private let stdout = FileHandle.standardOutput

    // ── Start capture ────────────────────────────────────────────────────────
    func start() async throws {

        // 1. Request permission / get shareable content
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false)

        // 2. Build a filter that captures the ENTIRE display (all apps)
        guard let display = content.displays.first else {
            fputs("AudioTap: no display found\n", stderr)
            exit(1)
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])

        // 3. Stream config — audio only, 16 kHz mono
        let config = SCStreamConfiguration()
        config.capturesAudio           = true
        config.sampleRate              = Int(kSampleRate)
        config.channelCount            = kChannels
        config.excludesCurrentProcessAudio = true

        // minimise video overhead (we don't need frames)
        config.minimumFrameInterval    = CMTime(value: 1, timescale: 1)
        config.width                   = 2
        config.height                  = 2

        // 4. Create and start the stream
        stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream!.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try await stream!.startCapture()

        fputs("AudioTap: capture started — writing PCM f32le 16 kHz mono to stdout\n", stderr)

        // Keep running until killed — use dispatchMain() instead of RunLoop.main.run()
        dispatchMain()
    }

    // ── Audio conversion helpers ─────────────────────────────────────────────
    private func makeConverter(from src: AVAudioFormat) -> AVAudioConverter? {
        guard let dst = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: kSampleRate,
            channels: AVAudioChannelCount(kChannels),
            interleaved: false)
        else { return nil }

        outputFormat = dst
        inputFormat  = src
        return AVAudioConverter(from: src, to: dst)
    }

    // ── Convert CMSampleBuffer → float32 PCM → stdout ────────────────────────
    private func process(_ sampleBuffer: CMSampleBuffer) {
        guard let desc = sampleBuffer.formatDescription else { return }

        // Fix 1: use 'var' so we can pass as inout
        var asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc)!.pointee
        guard let srcFormat = AVAudioFormat(streamDescription: &asbd) else { return }

        // Lazy-init converter when format is known
        if converter == nil || inputFormat != srcFormat {
            converter = makeConverter(from: srcFormat)
        }
        guard let conv = converter, let outFmt = outputFormat else { return }

        // Wrap CMSampleBuffer in AVAudioPCMBuffer
        guard let srcBuf = try? AVAudioPCMBuffer(pcmFormat: srcFormat, bufferListNoCopy: sampleBuffer) else { return }

        // Calculate output frame count after resampling
        let ratio       = outFmt.sampleRate / srcFormat.sampleRate
        let outFrames   = AVAudioFrameCount(Double(srcBuf.frameLength) * ratio + 1)
        guard let dstBuf = AVAudioPCMBuffer(pcmFormat: outFmt, frameCapacity: outFrames) else { return }

        // Fix 2: declare error as NSError? so status != .error compiles
        var convertError: NSError?
        let status = conv.convert(to: dstBuf, error: &convertError) { _, outStatus in
            outStatus.pointee = .haveData
            return srcBuf
        }

        guard status != .error, convertError == nil,
              let floatData = dstBuf.floatChannelData else { return }

        // Write raw float32-LE samples to stdout
        let count  = Int(dstBuf.frameLength)
        let ptr    = floatData[0]
        let data   = Data(bytes: ptr, count: count * MemoryLayout<Float>.size)
        stdout.write(data)
    }
}

// ── SCStreamDelegate ─────────────────────────────────────────────────────────
extension AudioTap: SCStreamDelegate {
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("AudioTap: stream stopped — \(error.localizedDescription)\n", stderr)
        exit(1)
    }
}

// ── SCStreamOutput ───────────────────────────────────────────────────────────
extension AudioTap: SCStreamOutput {
    func stream(_ stream: SCStream,
                didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio else { return }
        process(sampleBuffer)
    }
}

// ── AVAudioPCMBuffer convenience init from CMSampleBuffer ────────────────────
extension AVAudioPCMBuffer {
    convenience init?(pcmFormat: AVAudioFormat, bufferListNoCopy sampleBuffer: CMSampleBuffer) throws {
        var blockBuffer: CMBlockBuffer?
        var bufferListSize: Int = 0

        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: &bufferListSize,
            bufferListOut: nil,
            bufferListSize: 0,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: nil)

        let bufferListPtr = UnsafeMutablePointer<AudioBufferList>.allocate(capacity: bufferListSize)
        defer { bufferListPtr.deallocate() }

        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: bufferListPtr,
            bufferListSize: bufferListSize,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer)

        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        self.init(pcmFormat: pcmFormat, frameCapacity: frameCount)

        // Fix 3: mutableAudioBufferList is non-optional, remove guard let
        let ablPointer = self.mutableAudioBufferList
        let srcList = UnsafePointer<AudioBufferList>(bufferListPtr)
        let dstList = UnsafeMutableAudioBufferListPointer(ablPointer)
        let srcListPtr = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: srcList))

        for i in 0..<min(dstList.count, srcListPtr.count) {
            dstList[i] = srcListPtr[i]
        }
        self.frameLength = frameCount
    }
}

// ── Entry point ──────────────────────────────────────────────────────────────
let tap = AudioTap()
Task { try await tap.start() }
RunLoop.main.run()