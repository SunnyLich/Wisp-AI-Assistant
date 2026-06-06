import AVFoundation
import Foundation

@MainActor
final class AudioPlayer: NSObject, AVAudioPlayerDelegate {

    private var player: AVAudioPlayer?
    private var nextPlaybackID = 0
    private var activePlaybackID: Int?
    private var playbackIDs: [ObjectIdentifier: Int] = [:]
    var onFinish: ((Int, Bool) -> Void)?

    func play(url: URL) throws -> Int {
        stop()
        let player = try AVAudioPlayer(contentsOf: url)
        nextPlaybackID += 1
        let playbackID = nextPlaybackID
        playbackIDs[ObjectIdentifier(player)] = playbackID
        activePlaybackID = playbackID
        player.delegate = self
        player.prepareToPlay()
        player.play()
        self.player = player
        NSLog("[wisp] playing audio: %@", url.path)
        return playbackID
    }

    func stop() {
        if let player, player.isPlaying {
            player.stop()
        }
        if let player {
            playbackIDs.removeValue(forKey: ObjectIdentifier(player))
        }
        player = nil
        activePlaybackID = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        NSLog("[wisp] audio playback finished: %@", String(describing: flag))
        let key = ObjectIdentifier(player)
        Task { @MainActor in
            self.finish(key, successfully: flag)
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        NSLog("[wisp] audio playback decode error: %@", String(describing: error))
        let key = ObjectIdentifier(player)
        Task { @MainActor in
            self.finish(key, successfully: false)
        }
    }

    private func finish(_ key: ObjectIdentifier, successfully flag: Bool) {
        guard let playbackID = playbackIDs.removeValue(forKey: key) else { return }
        if activePlaybackID == playbackID {
            player = nil
            activePlaybackID = nil
            onFinish?(playbackID, flag)
        }
    }
}
