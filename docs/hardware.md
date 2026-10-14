# Hardware notes

## Servo

`--servo-pin` is a **BCM GPIO number**, not a physical header position. Framewire uses `gpiozero.AngularServo` with a nominal ±90° range. `--servo-range` limits the requested motion within that range; its default is ±60°. Actual travel differs between servos, so begin with a small range and increase it after checking for mechanical stops. `--invert-pan` changes direction. On target loss or camera failure, Framewire sets the servo angle to `None`, which releases PWM; the servo may no longer hold position.

A GPIO pin is a signal output, not a motor power source. Use a suitable external servo supply, shared ground and appropriate wiring. A lightweight desk lamp mechanism needs its own physical stops, cable routing and stable mount. See the [gpiozero AngularServo documentation](https://gpiozero.readthedocs.io/en/latest/api_output.html#angularservo) for calibration and pulse width behavior.

The mapping assumes the camera remains fixed while a separate lamp pans. A camera mounted on the moving servo changes the observed target position as the servo moves; that requires a feedback controller and calibration beyond this initial interface.

## Digital trigger

`--gpio-pin` uses `gpiozero.DigitalOutputDevice` and starts LOW. It is HIGH during target presence, and LOW after target loss, camera error or normal shutdown. Do not drive a lamp, motor or laser load directly from a GPIO pin; use a suitable driver. An independent physical interlock is required for any hazardous emitter. Software target detection is not a safety mechanism.

## Camera

`framewire doctor` reads one frame but does not actuate outputs. USB cameras need OS permissions and a compatible driver. Pi camera modules need a working libcamera/Picamera2 installation; installing the Python package alone cannot fix a disconnected ribbon cable or a disabled camera stack. For network sources, OpenCV uses FFmpeg for stream decoding; still-image snapshot URLs use an HTTP fetch limited to 8 MB with a 5-second timeout.

## Startup and failure state

Outputs initialize inactive. No physical output is opened until `framewire run` has explicit pin options. A detected target must persist for two frames before activation. Losing the camera releases outputs immediately; briefly missing a face retains the current target only for the configured grace period. On Ctrl+C or SIGTERM, Framewire releases and closes the outputs. Abrupt power loss or a process kill cannot execute software cleanup; circuit design must define its own safe power-off state.
