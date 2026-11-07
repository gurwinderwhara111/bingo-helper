"""Fake game window for testing the live loop without opening the real game.

Displays the card 1:1 in a topmost Tkinter window. The live app captures the
real screen, so it sees this exactly like a browser.
"""
import sys
import tkinter as tk

path = sys.argv[1] if len(sys.argv) > 1 else "test_oncard.png"

root = tk.Tk()
root.title("fake bingo")
root.attributes("-topmost", True)
root.configure(bg="black")
root.geometry("+40+40")

img = tk.PhotoImage(file=path)
tk.Label(root, image=img, bd=0).pack()
print(f"showing {img.width()} x {img.height()} at +40+40")
root.mainloop()
