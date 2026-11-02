"use client";

import { useEffect, useRef, useState } from "react";

import type { JarvisWakeGateMode } from "./benchmark-preferences";
import { JarvisFeed, type JarvisFeedSnapshot } from "./feed";
import { createJarvisVoiceSession, type JarvisVoiceSession, type JarvisVoiceState } from "./voice-session";

export interface UseJarvisOptions {
  remoteAudio: () => HTMLAudioElement | null;
  wakeGate: () => { developerMode: boolean; selected: JarvisWakeGateMode };
  onError: (message: string) => void;
}

export interface UseJarvis {
  feed: JarvisFeed | null;
  voice: JarvisVoiceSession | null;
  feedState: JarvisFeedSnapshot;
  voiceState: JarvisVoiceState | null;
}

const EMPTY_FEED = new JarvisFeed().current();

/**
 * The page's two owners for one mount: the feed (what is shown) and the voice
 * session (who talks). Each sees only the other's events it needs: durable live
 * events reach the transport, provider transcripts reach the feed. Unmounting
 * disposes both; on the phone that only stops observing Android.
 */
export function useJarvis(options: UseJarvisOptions): UseJarvis {
  const optionsRef = useRef(options);
  useEffect(() => { optionsRef.current = options; });
  const [owners, setOwners] = useState<{ feed: JarvisFeed; voice: JarvisVoiceSession } | null>(null);
  const [feedState, setFeedState] = useState<JarvisFeedSnapshot>(EMPTY_FEED);
  const [voiceState, setVoiceState] = useState<JarvisVoiceState | null>(null);

  useEffect(() => {
    let voice: JarvisVoiceSession | null = null;
    const feed = new JarvisFeed({
      onLiveEvent: (event) => voice?.recordLiveEvent(event),
      onStreamState: (state) => voice?.recordStreamState(state),
    });
    voice = createJarvisVoiceSession({
      remoteAudio: () => optionsRef.current.remoteAudio(),
      wakeGate: () => optionsRef.current.wakeGate(),
      onError: (message) => optionsRef.current.onError(message),
      onProviderEvent: (event) => feed.observeProviderEvent(event),
    });
    const offFeed = feed.subscribe(setFeedState);
    const offVoice = voice.subscribe(setVoiceState);
    setOwners({ feed, voice });
    setVoiceState(voice.current());
    feed.start();
    return () => {
      offFeed();
      offVoice();
      feed.dispose();
      voice?.dispose();
    };
  }, []);

  // Wake listening starts once today's log exists.
  const conversationId = feedState.state?.current_log?.id ?? null;
  useEffect(() => {
    owners?.voice.armWake(conversationId);
  }, [owners, conversationId]);

  return { feed: owners?.feed ?? null, voice: owners?.voice ?? null, feedState, voiceState };
}
