import FreeSimpleGUI as sg

layout = [
    [sg.Text("Dropdown 1:"), sg.Combo(["Option A", "Option B", "Option C"], default_value="Option A", key="-COMBO1-")],
    [sg.Text("Dropdown 2:"), sg.Combo(["Python", "Java", "C++"], default_value="Python", key="-COMBO2-")],
    [sg.Button("Exit")]
]

# 1. You MUST use finalize=True so the backend widgets are built immediately
window = sg.Window("Dropdown Font Fix", layout, finalize=True)

# 2. Set your desired font and size here
DESIRED_FONT = ("Helvetica", 22) 

# 3. Loop through your layout and force-apply the font directly to the hidden Tkinter popdowns
for row in layout:
    for element in row:
        if isinstance(element, sg.Combo):
            # Get the underlying Tkinter combobox widget
            combo_widget = element.Widget
            
            # Find the internal Tcl system name for this specific popdown menu
            popdown_name = combo_widget.tk.eval(f'ttk::combobox::PopdownWindow {combo_widget}')
            
            # Directly force the internal listbox ('.f.l') to use your font
            combo_widget.tk.call(f'{popdown_name}.f.l', 'configure', '-font', DESIRED_FONT)

# Standard Event Loop
while True:
    event, values = window.read()
    if event in (sg.WINDOW_CLOSED, "Exit"):
        break

window.close()