# iPhone Voice, step by step

The voice assistant of [PianaSoul Basic](../README.md#iphone-voice) with your iPhone as her microphone and speaker:
how to set it up, and how it behaves.

## Set up the iPhone (once)

The Mac and the iPhone need the same Wi-Fi. Safari lets a page use the microphone only over trusted HTTPS, so the
Mac makes its own small certificate authority, and the iPhone is told once to trust it.

1. **Install** the page's server. It uses the keys and voice settings from [Voice](../README.md#voice):

   ```bash
   uv pip install --python .venv/bin/python -e '.[web-voice]'
   ```

2. **Start it** on the Mac:

   ```bash
   ./run.sh web-voice
   ```

   It prints the page's address on its `address` line, with the Mac's network address in it, for example
   `https://192.168.1.20:8766/#token=...`. If macOS asks whether Python may accept incoming connections, allow it.
   Control+C stops it.

3. **Trust the Mac on the iPhone.** The first start also makes `output/.cache/phone-tls/PianaSoul-Basic-CA.cer`,
   shows it in Finder and lists these steps. AirDrop that file, and only that one, to the iPhone (right-click →
   Share → AirDrop), and install it in Settings → General → VPN & Device Management. Then turn on full trust for it
   ("PianaSoul Basic on" and the Mac's name) in Settings → General → About → Certificate Trust Settings.

4. **Open the page** at the printed address in Safari on the iPhone. Share → **Add to Home Screen** makes it an app
   that keeps this address. Open the app, tap the orb and allow the microphone.

After that, start the server and open the app.

<details>
<summary><b>When the Mac's address changes, and another iPhone</b></summary>

Every start makes a fresh certificate for the Mac's current network address, so the iPhone never needs the
certificate again. If the router gives the Mac a new address, the printed address changes with it; add the new one
to the Home Screen. A DHCP reservation for the Mac in the router keeps the address fixed, as long as the Mac's
Private Wi-Fi Address for that network (System Settings → Wi-Fi → Details) is not set to Rotating. The page is
opened at the address rather than at the Mac's `.local` name because Safari does not keep the microphone permission
for `.local` names, and would ask every time. For another iPhone,
`open -R output/.cache/phone-tls/PianaSoul-Basic-CA.cer` shows the file again.

</details>

<details>
<summary><b>What to keep private</b></summary>

The `.pem` files and the token, all in `output/`, which is never committed. With the token, anyone on your Wi-Fi
could use the page. The certificate authority can vouch only for `.local` names and home network addresses, so even
its key could not pass anything off as a website. `PianaSoul-Basic-CA.cer` holds no secret. To start afresh, delete
`output/.cache/phone-token` (a new address, to add to the Home Screen again) or `output/.cache/phone-tls/` (a new
certificate authority, to install again).

</details>

## Using it

- **Tap the orb** to start. It is half colored while it starts (more taps do not cancel it), then turns to color
  with a start sound. From then on it is the same conversation as `./run.sh voice`, the same code, with the phone as
  her ears and voice.
- **Tap again to pause.** She stops listening and speaking at once, and the piano about a second later, once the
  notes already sent have played (see [Timing](../README.md#timing)). A task in progress finishes only its current step; the
  microphone is released after the end sound. Tap to continue from the same place in the same conversation.
- **Closing the page**, locking the phone or switching apps ends the conversation and stops the music. The orb is
  gray again the next time.

<details>
<summary><b>What the Mac does, and what the page cannot do</b></summary>

The Mac does the rest, with the same keys and settings as `./run.sh voice`: it listens for her name, sends what
follows it to speech recognition, asks the assistant, and sends her voice to the phone while it is being made.
Nothing is played on the Mac's speakers and its microphone is not used; the piano plays through your usual MIDI
route. One phone at a time, and not alongside `./run.sh voice` or `./run.sh chat`. You cannot interrupt her by
speaking, but a tap pauses her. The page has to stay in front; it keeps the screen on while it is connected, and
does not zoom or scroll. In portrait the iPhone still shows its status bar, which only native apps can hide.
`./run.sh web-voice --debug` shows each time she hears her name, as with `./run.sh voice --debug`.

</details>
