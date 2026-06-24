import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreAudio

let kSampleRate: Double = 16000
let kChannels:   Int    = 1

final class AudioTap: NSObject {
    private var stream: SCStream?
    private let queue = DispatchQueue(label: "audio.tap.queue", qos: .userInteractive)
    private var converter: AVAudioConverter?
    private var inputFormat:  AVAudioFormat?
    private var outputFormat: AVAudioFormat?
    private let stdout = FileHandle.standardOutput

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            fputs("AudioTap: no display found\n", stderr); exit(1)
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.sampleRate = Int(kSampleRate)
        config.channelCount = kChannels
        config.excludesCurrentProcessAudio = false
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.width = 2
        config.height = 2

        stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream!.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try await stream!.startCapture()
        fputs("AudioTap: capture started — writing PCM f32le 16 kHz mono to stdout\n", stderr)
        try await Task.sleep(nanoseconds: UInt64.max)
    }

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

    private func process(_ sampleBuffer: CMSampleBuffer) {
        guard let desc = sampleBuffer.formatDescription else { return }
        var asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc)!.pointee
        guard let srcFormat = AVAudioFormat(streamDescription: &asbd) else { return }

        if converter == nil || inputFormat != srcFormat {
            converter = makeConverter(from: srcFormat)
        }
        guard let conv = converter, let outFmt = outputFormat else { return }

        // Get AudioBufferList from CMSampleBuffer directly
        var blockBuffer: CMBlockBuffer?
        var audioBufferList = AudioBufferList()
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: &audioBufferList,
            bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer)

        guard status == noErr else {
            fputs("AudioTap: failed to get buffer list \(status)\n", stderr)
            return
        }

        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard let srcBuf = AVAudioPCMBuffer(pcmFormat: srcFormat, frameCapacity: frameCount) else { return }
        srcBuf.frameLength = frameCount

        // Copy audio data manually into srcBuf
        let ablPointer = UnsafeMutableAudioBufferListPointer(&audioBufferList)
        let dstPointer = UnsafeMutableAudioBufferListPointer(srcBuf.mutableAudioBufferList)
        for i in 0..<min(ablPointer.count, dstPointer.count) {
            let src = ablPointer[i]
            let dst = dstPointer[i]
            if let srcData = src.mData, let dstData = dst.mData {
                memcpy(dstData, srcData, Int(src.mDataByteSize))
            }
        }

        let ratio     = outFmt.sampleRate / srcFormat.sampleRate
        let outFrames = AVAudioFrameCount(Double(srcBuf.frameLength) * ratio + 1)
        guard let dstBuf = AVAudioPCMBuffer(pcmFormat: outFmt, frameCapacity: outFrames) else { return }

        var convertError: NSError?
        let convStatus = conv.convert(to: dstBuf, error: &convertError) { _, outStatus in
            outStatus.pointee = .haveData
            return srcBuf
        }

        guard convStatus != .error, convertError == nil,
              let floatData = dstBuf.floatChannelData else {
            fputs("AudioTap: conversion error \(String(describing: convertError))\n", stderr)
            return
        }

        let count = Int(dstBuf.frameLength)
        let ptr   = floatData[0]
        let data  = Data(bytes: ptr, count: count * MemoryLayout<Float>.size)
        stdout.write(data)
    }
}

extension AudioTap: SCStreamDelegate {
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("AudioTap: stream stopped — \(error.localizedDescription)\n", stderr)
        exit(1)
    }
}

extension AudioTap: SCStreamOutput {
    func stream(_ stream: SCStream,
                didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio else { return }
        process(sampleBuffer)
    }
}

let tap = AudioTap()
Task { try await tap.start() }
RunLoop.main.run()
