"""Live GNU Radio flowgraph for showing the judges (optional; needs radioconda with Qt).

    <radioconda>\\python.exe scripts\\gr_live_demo.py [barrage|narrowband|sweep|none]

Opens a waterfall and a spectrum view of the receiver chain the simulation uses:
QPSK modulator (2 Msym/s, RRC 0.35) -> rotator onto channel 2 -> Rician fading model
-> + jammer -> + thermal noise -> throttle -> Qt waterfall / frequency sink (80 MHz span at 2445 MHz).
"""
import sys

import numpy as np
from gnuradio import analog, blocks, channels, digital, gr, qtgui
from gnuradio.fft import window
from PyQt5 import Qt
import sip  # noqa: F401  (needed by qtgui on some installs)

FS = 80e6
FC = 2445e6
SPS = 40


class AegisChain(gr.top_block, Qt.QWidget):
    def __init__(self, jammer: str):
        gr.top_block.__init__(self, "ABHEDYA receiver chain")
        Qt.QWidget.__init__(self)
        self.setWindowTitle(f"ABHEDYA · GNU Radio chain · jammer: {jammer}")
        layout = Qt.QVBoxLayout(self)

        src = analog.random_uniform_source_b(0, 256, 0)
        mod = digital.generic_mod(digital.constellation_qpsk().base(), False, SPS, True, 0.35, False, False)
        rot = blocks.rotator_cc(2 * np.pi * (-15e6) / FS)                     # channel 2 = 2430 MHz
        fade = channels.fading_model(8, 1e-6, True, 8.0, 7)
        sig = blocks.multiply_const_cc(0.05)
        noise = analog.noise_source_c(analog.GR_GAUSSIAN, 0.002, 1)
        add = blocks.add_cc()
        throttle = blocks.throttle(gr.sizeof_gr_complex, FS / 50)
        wf = qtgui.waterfall_sink_c(1024, window.WIN_BLACKMAN_hARRIS, FC, FS, "Waterfall", 1, None)
        fs = qtgui.freq_sink_c(1024, window.WIN_BLACKMAN_hARRIS, FC, FS, "Spectrum", 1, None)
        fs.set_y_axis(-110, -20)
        layout.addWidget(sip.wrapinstance(fs.qwidget(), Qt.QWidget))
        layout.addWidget(sip.wrapinstance(wf.qwidget(), Qt.QWidget))

        self.connect(src, mod, rot, fade, sig, (add, 0))
        self.connect(noise, (add, 1))
        if jammer == "barrage":
            self.connect(analog.noise_source_c(analog.GR_GAUSSIAN, 0.02, 2), (add, 2))
        elif jammer == "narrowband":
            nb = analog.noise_source_c(analog.GR_GAUSSIAN, 0.08, 3)
            from gnuradio.filter import firdes, freq_xlating_fir_filter_ccc
            filt = freq_xlating_fir_filter_ccc(1, firdes.low_pass(1.0, FS, 2.5e6, 1e6), -15e6, FS)
            self.connect(nb, filt, (add, 2))
        elif jammer == "sweep":
            # VCO driven by a sawtooth: sweeps the full band every 1.2 ms
            saw = analog.sig_source_f(FS, analog.GR_SAW_WAVE, 1 / 1.2e-3, 1.0, -0.5)
            vco = blocks.vco_c(FS, 2 * np.pi * FS, 0.05)
            self.connect(saw, vco, (add, 2))
        self.connect(add, throttle, wf)
        self.connect(throttle, fs)


def main():
    jammer = sys.argv[1] if len(sys.argv) > 1 else "narrowband"
    app = Qt.QApplication(sys.argv)
    tb = AegisChain(jammer)
    tb.start()
    tb.show()
    app.aboutToQuit.connect(lambda: (tb.stop(), tb.wait()))
    app.exec_()


if __name__ == "__main__":
    main()
