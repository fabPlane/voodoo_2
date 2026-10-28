// Synthesis-cost test: minimal AE350 (boot ROM + data RAM on fabric AHB,
// UART2, PIT, SMU, one extended AHB slave for the Voodoo register bus).
`define AE350_EXT_I_MEM_EN
`define AE350_EXT_D_MEM_EN
`define AE350_EXT_AHB_SLAVE_EN
`define AE350_APB_UART2_EN
`define AE350_APB_PIT_EN
`define AE350_APB_SMU_EN
