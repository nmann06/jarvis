// What the light means, in words. The colour is never the only signal.
export const LIGHT_TEXT = {
  listening: ["Listening", "You are talking to Jarvis."],
  thinking: ["Thinking", "Jarvis is working on an answer."],
  speaking: ["Speaking", "Jarvis is talking."],
  idle: ["Idle", "Light off. Say “Hey Jarvis” to wake it."],
  unknown: ["Unknown", "The panel can't tell what Jarvis is doing right now."],
};

// Why there's no light, keyed on the server's hooks_reason. Only needs_restart asks for a restart.
export const NO_LIGHT = {
  needs_restart: "Jarvis is running without the voice hooks, so there's no light yet. It needs a restart.",
  voice_off: "Voice is turned off in Settings, so there's no light.",
  starting: "Jarvis is starting up and loading its models. The light comes on in a minute or two.",
  down: "Jarvis isn't running.",
};

export function lightWords(store) {
  if (store.conn === "lost") return ["Unknown", "Lost the connection to the Jarvis machine. Trying again every few seconds."];
  if (store.conn === "unauth") return ["Unknown", "This browser isn't signed in to the panel."];
  if (store.conn === "connecting") return ["Unknown", "Connecting…"];
  if (!store.hooks) return ["Unknown", NO_LIGHT[store.hooksReason] || NO_LIGHT.needs_restart];
  return LIGHT_TEXT[store.light] || LIGHT_TEXT.unknown;
}

export function applyLight(el, store) {
  const state = store.shownLight;
  el.dataset.state = state;
  el.setAttribute("aria-label", `Light: ${lightWords(store)[0].toLowerCase()}`);
}
