import AVFoundation
import Foundation

@MainActor
final class AudioPlayer: NSObject, AVAudioPlayerDelegate {

    private var player: AVAudioPlayer?

    func play(url: URL) throws {
        stop()
        let player = try AVAudioPlayer(contentsOf: url)
        player.delegate = self
        player.prepareToPlay()
        player.play()
        self.player = player
        NSLog("[wisp] playing audio: %@", url.path)
    }

    func stop() {
        if let player, player.isPlaying {
            player.stop()
        }
        player = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        NSLog("[wisp] audio playback finished: %@", String(describing: flag))
    }
}
