package com.example;

import java.io.File;
import java.util.Locale;
import javax.sound.sampled.AudioInputStream;
import javax.sound.sampled.AudioSystem;
import javax.sound.sampled.Clip;

public class MusicPlayer {
    private Clip clip;

    public void play(File file) throws Exception {
        try (AudioInputStream audio = AudioSystem.getAudioInputStream(file)) {
            clip = AudioSystem.getClip();
            clip.open(audio);
            clip.start();
        }
    }

    public void stop() {
        if (clip != null) {
            clip.stop();
        }
    }

    public boolean supportsExtension(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        return lower.endsWith(".wav");
    }

    public static void main(String[] args) {
        System.out.println("MusicPlayer");
    }
}
