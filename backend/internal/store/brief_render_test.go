package store

import (
	"context"
	"strings"
	"testing"
)

func TestFR_H0_UI_2_RenderTextPersistsAndOldItemsStayCompatible(t *testing.T) {
	s, intern, _, _, _ := dataStore(t)
	ctx := context.Background()
	input := stageFourBrief("worded")
	input.Items[0].RenderText = "Synthetic short wording"
	got, err := s.SaveBrief(ctx, intern, input)
	if err != nil || got.Items[0].RenderText != input.Items[0].RenderText {
		t.Fatal(got, err)
	}
	loaded, err := s.Brief(ctx, intern, input.ID)
	if err != nil || loaded.Items[0].RenderText != input.Items[0].RenderText || loaded.Items[0].URL != input.Items[0].URL {
		t.Fatal(loaded, err)
	}
	legacy, err := s.SaveBrief(ctx, intern, stageFourBrief("legacy"))
	if err != nil || legacy.Items[0].RenderText != "" {
		t.Fatal(legacy, err)
	}
	input.ID = "too-long-wording"
	input.Items[0].RenderText = strings.Repeat("x", 2001)
	if _, err := s.SaveBrief(ctx, intern, input); err != ErrInvalid {
		t.Fatal(err)
	}
}
