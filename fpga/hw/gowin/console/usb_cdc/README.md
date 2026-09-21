USB CDC ACM debug gadget for the Tang Retro Console 138K.

Vendored from Sipeed `TangMega-138KPro-example` `usb2_soft/usb_uart_loopback`
(Apache-2.0, see SIPEED_APACHE_2.0.txt).

Console S1 Type-C D+/D- reaches the full-speed GPIO SoftPHY through
USB_TERM_RXDP/RXDN J16/AB16 and R130/R131 (3 Ω). USB_DX J19/H19 is the
separate analog-HS path through R132/R133 (47.5 Ω). The analog HS SoftPHY
(`usb_cdc/hs/usb2_0_softphy.v`, 960 MHz `fclk`) synthesized but left 1875
nets unrouted.
