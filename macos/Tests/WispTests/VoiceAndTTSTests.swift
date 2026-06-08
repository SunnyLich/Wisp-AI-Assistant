import XCTest
import Foundation

final class VoiceAndTTSTests: XCTestCase {

    func testAudioRecorderKeepsNativeMicrophoneCaptureContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Audio/AudioRecorder.swift"),
            encoding: .utf8
        )

        for expected in [
            "AVCaptureDevice.authorizationStatus(for: .audio)",
            "AVCaptureDevice.requestAccess(for: .audio)",
            "AVAudioEngine()",
            "input.installTap(onBus: 0",
            "AVAudioFile(forWriting: url, settings: settings)",
            "AVFormatIDKey: kAudioFormatLinearPCM",
            "RunLogLocator.writableLogDirectory()",
            "voice-\\(stamp).wav",
        ] {
            XCTAssertTrue(source.contains(expected), "AudioRecorder is missing \(expected).")
        }
    }

    func testAudioPlayerFeedsPlaybackAmplitudeToCallbacks() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Audio/AudioPlayer.swift"),
            encoding: .utf8
        )

        for expected in [
            "AVAudioPlayer(contentsOf: url)",
            "player.isMeteringEnabled = true",
            "startAmplitudeMeter(for: playbackID)",
            "Timer.scheduledTimer(withTimeInterval: 1.0 / 24.0",
            "player.updateMeters()",
            "Self.normalizedAmplitude(averagePower: player.averagePower(forChannel: 0))",
            "self.onAmplitude?(playbackID, amplitude)",
            "onFinish?(playbackID, flag)",
        ] {
            XCTAssertTrue(source.contains(expected), "AudioPlayer is missing \(expected).")
        }
    }

    func testAppDelegateKeepsVoiceQueryAndTTSWorkflowWired() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        for expected in [
            "private let audioRecorder = AudioRecorder()",
            "private let audioPlayer = AudioPlayer()",
            "audioPlayer.onFinish = { [weak self] playbackID, success in",
            "audioPlayer.onAmplitude = { [weak self] playbackID, amplitude in",
            "startVoiceQuery()",
            "stopVoiceQuery()",
            "pendingVoiceContext = PendingNativeContext",
            "overlay?.setState(.listening)",
            "responseBubble?.showListening(anchor: overlay?.frame)",
            "let url = try await audioRecorder.start()",
            "let recording = try audioRecorder.stop()",
            "let transcript = try await transcribe(recording.url)",
            "await self.runPrompt(",
            "contextSnapshot: voiceContext?.snapshot",
            "speakLastResponse()",
            "let url = try await synthesizeSpeech(text)",
            "let playbackID = try audioPlayer.play(url: url)",
            "activeTTSPlaybackID = playbackID",
            "overlay?.setSpeechAmplitude(amplitude)",
            "overlay?.setSpeechAmplitude(0)",
            "brain.transcribe",
            "brain.tts.synthesize",
            "brain.tts.test",
        ] {
            XCTAssertTrue(source.contains(expected), "AppDelegate voice/TTS wiring is missing \(expected).")
        }
    }

    private func sourceRoot() -> URL {
        sourceRoot(from: URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
    }

    private func sourceRoot(from currentDirectory: URL) -> URL {
        if currentDirectory.lastPathComponent == "macos" {
            return currentDirectory
        }
        return currentDirectory.appendingPathComponent("macos")
    }
}
