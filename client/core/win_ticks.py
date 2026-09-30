"""How long ago, from two Windows tick counts — with the wrap handled.

WHY THIS IS NOT A SUBTRACTION. GetTickCount and LASTINPUTINFO.dwTime are both
32-bit counts of milliseconds since the machine booted, so they roll over to
zero every 49.7 days. Subtracted plainly, the moment the counter wraps while
somebody's last input is still on the far side of it, the answer goes NEGATIVE
— and a negative "seconds since last input" is below every idle threshold, so
a Windows machine that has been up for seven weeks quietly stops reporting
anybody as idle until they touch the keyboard again.

The mask is the documented way to do this: the difference of two unsigned
32-bit counters, taken in 32-bit, is correct across exactly one wrap.
"""

TICK_WRAP = 1 << 32


def ticks_ago_ms(now_ticks: int, last_input_ticks: int) -> int:
    """Milliseconds between the two, in the order they happened."""
    return (int(now_ticks) - int(last_input_ticks)) % TICK_WRAP
