"use client";

import { useLayoutEffect, useRef, type RefObject } from "react";


export function useConversationAutoScroll(
  containerRef: RefObject<HTMLElement | null>,
  ready: boolean,
  conversationId: string | null,
  newestMessageId: string | null,
): void {
  const initializedRef = useRef(false);
  const conversationIdRef = useRef<string | null>(null);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!ready || !container) return;

    const sameConversation = initializedRef.current
      && conversationIdRef.current === conversationId;
    container.scrollTo({
      top: container.scrollHeight,
      behavior: sameConversation ? "smooth" : "auto",
    });
    initializedRef.current = true;
    conversationIdRef.current = conversationId;
  }, [containerRef, conversationId, newestMessageId, ready]);
}
