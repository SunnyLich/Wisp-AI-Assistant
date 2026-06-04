import AVFoundation
import Foundation

struct RecordingResult {
    var url: URL
    var duration: TimeInterval

    var displayText: String {
        """
        Recording saved:
        \(url.path)

        Duration: \(String(format: "%.2f", duration))s
        """
    }
}

enum AudioRecorderError: Error, CustomStringConvertible {
    case microphoneDenied
    case alreadyRecording
    case notRecording
    case noInputFormat
    case fileUnavailable

    var description: String {
        switch self {
        case .microphoneDenied:
            return "Microphone permission is denied or not granted"
        case .alreadyRecording:
            return "recording is already active"
        case .notRecording:
            return "recording is not active"
        case .noInputFormat:
            return "microphone input format is unavailable"
        case .fileUnavailable:
            return "recording file is unavailable"
        }
    }
}

@MainActor
final class AudioRecorder {

    private let engine = AVAudioEngine()
    private var audioFile: AVAudioFile?
    private var recordingURL: URL?
    private var startTime: Date?

    var isRecording: Bool {
        engine.isRunning
    }

    func requestMicrophoneAccess() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return true
        case .denied, .restricted:
            return false
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    continuation.resume(returning: granted)
                }
            }
        @unknown default:
            return false
        }
    }

    func start() async throws -> URL {
        guard !isRecording else { throw AudioRecorderError.alreadyRecording }
        guard await requestMicrophoneAccess() else { throw AudioRecorderError.microphoneDenied }

        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.channelCount > 0, format.sampleRate > 0 else {
            throw AudioRecorderError.noInputFormat
        }

        let url = outputURL()
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: format.sampleRate,
            AVNumberOfChannelsKey: Int(format.channelCount),
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]
        let file = try AVAudioFile(forWriting: url, settings: settings)

        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            do {
                try file.write(from: buffer)
            } catch {
                NSLog("[wisp] audio write failed: %@", String(describing: error))
            }
        }

        audioFile = file
        recordingURL = url
        startTime = Date()

        engine.prepare()
        try engine.start()
        NSLog("[wisp] recording started: %@", url.path)
        return url
    }

    func stop() throws -> RecordingResult {
        guard isRecording else { throw AudioRecorderError.notRecording }
        guard let url = recordingURL else { throw AudioRecorderError.fileUnavailable }

        engine.inputNode.removeTap(onBus: 0)
        engine.stop()

        let duration = startTime.map { Date().timeIntervalSince($0) } ?? 0
        audioFile = nil
        recordingURL = nil
        startTime = nil

        NSLog("[wisp] recording stopped: %@ (%.2fs)", url.path, duration)
        return RecordingResult(url: url, duration: duration)
    }

    private func outputURL() -> URL {
        let env = ProcessInfo.processInfo.environment
        let base: URL
        if let logDir = env["WISP_RUN_LOG_DIR"], !logDir.isEmpty {
            base = URL(fileURLWithPath: logDir)
        } else {
            base = FileManager.default.temporaryDirectory
        }

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        let stamp = formatter.string(from: Date())
        return base.appendingPathComponent("voice-\(stamp).wav")
    }
}
