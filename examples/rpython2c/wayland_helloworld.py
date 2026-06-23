"""A pure-rpython Wayland GUI -- the same window as the hand-written C demo,
but with zero hand-written glue. py2c generates the Wayland runtime.

    import rwayland   # py2c emits rwayland_rt.{h,c} + scans xdg-shell, links wl
"""
import rwayland

WIDTH = 400
HEIGHT = 300
HEADER_HEIGHT = 30


class DemoWindow(rwayland.Window):
    def __init__(self):
        super().__init__(WIDTH, HEIGHT, "rpython Wayland")
        self.button_pressed = 0

    def on_paint(self, fb: "u32*") -> None:
        rwayland.fill_rect(fb, WIDTH, HEIGHT, 0, 0, WIDTH, HEIGHT, 0xFFE0E0E0)
        rwayland.fill_rect(fb, WIDTH, HEIGHT, 0, 0, WIDTH, HEADER_HEIGHT, 0xFF404040)
        rwayland.fill_rect(fb, WIDTH, HEIGHT, WIDTH - 25, 5, 20, 20, 0xFFFF0000)
        if self.button_pressed:
            btn_color = 0xFF00AA00
        else:
            btn_color = 0xFF0088FF
        rwayland.fill_rect(fb, WIDTH, HEIGHT, 125, 130, 150, 40, btn_color)

    def on_pointer_button(self, x: int, y: int, pressed: int) -> int:
        if pressed:
            if x >= WIDTH - 25 and x <= WIDTH - 5 and y >= 5 and y <= 25:
                return rwayland.ACTION_QUIT
            elif y <= HEADER_HEIGHT:
                return rwayland.ACTION_MOVE
            elif x >= 125 and x <= 275 and y >= 130 and y <= 170:
                self.button_pressed = 1
                return rwayland.ACTION_REDRAW
        else:
            self.button_pressed = 0
            return rwayland.ACTION_REDRAW
        return rwayland.ACTION_NONE


def main() -> int:
    win = DemoWindow()
    return win.run()
