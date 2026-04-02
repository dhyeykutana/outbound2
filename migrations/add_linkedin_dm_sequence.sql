-- Migration: Add 3-message LinkedIn DM sequence columns to pipeline_results
-- Run once against your MySQL database.
-- The legacy `linkedin_dm` column is kept intact so existing rows are not affected.

ALTER TABLE pipeline_results
    ADD COLUMN li_dm1 LONGTEXT NULL COMMENT 'LinkedIn DM 1 – connection request (pattern interrupt, no Calyxr)' AFTER linkedin_dm,
    ADD COLUMN li_dm2 LONGTEXT NULL COMMENT 'LinkedIn DM 2 – follow-up after connect (introduce Calyxr + proof point)' AFTER li_dm1,
    ADD COLUMN li_dm3 LONGTEXT NULL COMMENT 'LinkedIn DM 3 – final touchpoint (ROI angle + low-commitment close)' AFTER li_dm2;
