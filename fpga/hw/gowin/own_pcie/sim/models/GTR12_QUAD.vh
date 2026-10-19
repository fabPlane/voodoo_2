// Quad model: lane PLLs lock 2 us after power-up; everything else idle.
reg cmu_ok_r = 1'b0;
initial #2_000_000 cmu_ok_r = 1'b1;
assign FABRIC_LANE0_CMU_OK_O = cmu_ok_r;
assign FABRIC_LANE1_CMU_OK_O = cmu_ok_r;
assign FABRIC_LANE2_CMU_OK_O = cmu_ok_r;
assign FABRIC_LANE3_CMU_OK_O = cmu_ok_r;
assign FABRIC_CMU_OK_O       = cmu_ok_r;
assign FABRIC_LN0_RXDET_RESULT  = cmu_ok_r;
assign FABRIC_LN0_PMA_RX_LOCK_O = cmu_ok_r;
assign FABRIC_LN0_ASTAT_O    = 6'd0;
assign FABRIC_LN0_RXDATA_O   = 88'd0;
assign FABRIC_LN1_RXDATA_O   = 88'd0;
assign FABRIC_LN2_RXDATA_O   = 88'd0;
assign FABRIC_LN3_RXDATA_O   = 88'd0;
assign FABRIC_LN0_RX_VLD_OUT = 1'b0;
