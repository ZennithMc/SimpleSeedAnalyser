# re-exports tests/diff_engine.py's evaluators with a settable MC
import importlib.util, os
_spec = importlib.util.spec_from_file_location("_de", os.path.join(os.path.dirname(__file__), "diff_engine.py"))
_de = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_de)
MC = 19
def c_eval(conds, seeds, lib):
    _de.MC = MC; return _de.c_eval(conds, seeds, lib)
def py_eval(conds, seeds, lib):
    _de.MC = MC; return _de.py_eval(conds, seeds, lib)
