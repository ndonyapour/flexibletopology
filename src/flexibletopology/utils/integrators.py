import numpy as np
import openmm.app as omma
import openmm.openmm as omm
from openmm import unit
import math


class CustomLPIntegrator(omm.CustomIntegrator):

    GLOBAL_PARAMETERS = ['lambda', 'charge', 'sigma', 'epsilon']
    def __init__(self, n_ghosts, timestep=1.0 * unit.femtoseconds,
                 coeffs=None, bounds=None):

        super(CustomLPIntegrator, self).__init__(timestep)
        assert coeffs is not None, "Coefficients must be given."
        assert bounds is not None, "Parameter bounds must be given."

        # initialize
        self.addPerDofVariable("x0", 0)

        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addGlobalVariable(f"f{parameter_name}_g{idx}", 1.0)
                self.addGlobalVariable(f"v{parameter_name}_g{idx}", 0.0)

        self.addGlobalVariable("coeffs_charge", coeffs['charge'])
        self.addGlobalVariable("coeffs_sigma", coeffs['sigma'])
        self.addGlobalVariable("coeffs_epsilon", coeffs['epsilon'])
        self.addGlobalVariable("coeffs_lambda", coeffs['lambda'])

        self.addUpdateContextState()
        # calcuate new positions and velocities
        self.addComputePerDof("x0", "x")
        self.addComputePerDof("v", "v+dt*f/m")

        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addComputeGlobal(f"f{parameter_name}_g{idx}",
                                      f"-deriv(energy, {parameter_name}_g{idx})")

        self.addComputePerDof("x", "x+dt*v")
        self.addConstrainPositions()
        self.addComputePerDof("v", "(x-x0)/dt")
        # parameters
        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addComputeGlobal(f"v{parameter_name}_g{idx}",
                                      f"v{parameter_name}_g{idx}+dt*"
                                      f"f{parameter_name}_g{idx}/"
                                      f"coeffs_{parameter_name}")

                self.addComputeGlobal(f"{parameter_name}_g{idx}",
                                      f"max(min({parameter_name}_g{idx}+dt*"
                                      f"v{parameter_name}_g{idx},"
                                      f"{bounds[parameter_name][1]}),"
                                      f"{bounds[parameter_name][0]})")


class CustomVerletIntegrator(omm.CustomIntegrator):

    GLOBAL_PARAMETERS = ['lambda', 'charge', 'sigma', 'epsilon']

    def __init__(self, n_ghosts, timestep=1.0 * unit.femtoseconds,
                 coeffs=None, bounds=None):

        super(CustomVerletIntegrator, self).__init__(timestep)

        assert coeffs is not None, "Coefficients must be given."
        assert bounds is not None, "Parameter bounds must be given."

        # variable initialization
        self.addPerDofVariable("x1", 0)

        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addGlobalVariable(f"f{parameter_name}_g{idx}", 1.0)
                self.addGlobalVariable(f"v{parameter_name}_g{idx}", 0.0)

        self.addGlobalVariable("coeffs_charge", coeffs['charge'])
        self.addGlobalVariable("coeffs_sigma", coeffs['sigma'])
        self.addGlobalVariable("coeffs_epsilon", coeffs['epsilon'])
        self.addGlobalVariable("coeffs_lambda", coeffs['lambda'])

        self.addUpdateContextState()

        self.addComputePerDof("v", "v+0.5*dt*f/m")
        self.addComputePerDof("x", "x+dt*v")
        self.addComputePerDof("x1", "x")
        self.addConstrainPositions()
        self.addComputePerDof("v", "v+0.5*dt*f/m+(x-x1)/dt")
        self.addConstrainVelocities()

        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addComputeGlobal(f"f{parameter_name}_g{idx}",
                                      f"-deriv(energy, {parameter_name}_g{idx})")

        for idx in range(n_ghosts):
            for parameter_name in self.GLOBAL_PARAMETERS:
                self.addComputeGlobal(f"v{parameter_name}_g{idx}",
                                      f"v{parameter_name}_g{idx}+0.5*dt+"
                                      f"f{parameter_name}_g{idx}/coeffs_{parameter_name}")

                self.addComputeGlobal(f"{parameter_name}_g{idx}",
                                      f"max(min({parameter_name}_g{idx}+dt*v{parameter_name}_g{idx},"
                                      f"{bounds[parameter_name][1]}),{bounds[parameter_name][0]})")


class CustomHybridIntegrator(omm.CustomIntegrator):

    GLOBAL_PARAMETERS = ['charge', 'sigma', 'epsilon', 'lambda']

    def __init__(self, n_ghosts, temperature, friction_coeff, timestep,
                 attr_fric_coeffs=None, attr_bounds=None):

        super(CustomHybridIntegrator, self).__init__(timestep)

        assert attr_fric_coeffs is not None, "Coefficients must be given."
        assert attr_bounds is not None, "Parameter bounds must be given."

        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addGlobalVariable(f"f{parameter_name}_g{idx}", 1.0)

        # check on this boltzmann constant (kJ/mol/K)
        self.addGlobalVariable("kT", (0.008314463*temperature))

        # Add a part for Langevin integrator for the molecules in the system!
        self.addGlobalVariable("a", math.exp(-friction_coeff*timestep))
        self.addGlobalVariable("b", math.sqrt(
            1 - math.exp(-2*friction_coeff*timestep)))
        self.addPerDofVariable("x1", 0)
        self.addUpdateContextState()
        self.addComputePerDof("v", "v + dt*f/m")
        self.addConstrainVelocities()

        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addComputeGlobal(f"f{parameter_name}_g{idx}",
                                      f"-deriv(energy, {parameter_name}_g{idx})")

        self.addComputePerDof("x", "x + 0.5*dt*v")
        self.addComputePerDof("v", "a*v + b*sqrt(kT/m)*gaussian")
        self.addComputePerDof("x", "x + 0.5*dt*v")
        self.addComputePerDof("x1", "x")
        self.addConstrainPositions()
        self.addComputePerDof("v", "v + (x-x1)/dt")

        for idx in range(n_ghosts):
            for parameter_name in self.GLOBAL_PARAMETERS:
                self.addComputeGlobal(f"{parameter_name}_g{idx}",
                                      f"max(min({parameter_name}_g{idx}"
                                      f"+dt*f{parameter_name}_g{idx}/{attr_fric_coeffs[parameter_name]}"
                                      f"+sqrt(2*kT*dt/{attr_fric_coeffs[parameter_name]})*gaussian,"
                                      f"{attr_bounds[parameter_name][1]}),{attr_bounds[parameter_name][0]})")


class CustomHybridIntegratorConstCharge(omm.CustomIntegrator):

    GLOBAL_PARAMETERS = ['charge', 'sigma', 'epsilon', 'lambda']

    def __init__(self, n_ghosts, temperature, friction_coeff, timestep,
                 attr_fric_coeffs=None, attr_bounds=None, const_charge=0.):

        super(CustomHybridIntegratorConstCharge, self).__init__(timestep)

        assert attr_fric_coeffs is not None, "Coefficients must be given."
        assert attr_bounds is not None, "Parameter bounds must be given."

        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addGlobalVariable(f"f{parameter_name}_g{idx}", 1.0)

        # define a global variable for the total charge
        self.addGlobalVariable("tot_charge", 0.0)

        # check on this boltzmann constant (kJ/mol/K)
        self.addGlobalVariable("kT", (0.008314463*temperature))

        # Add a part for Langevin integrator for the molecules in the system!
        self.addGlobalVariable("a", math.exp(-friction_coeff*timestep))
        self.addGlobalVariable("b", math.sqrt(
            1 - math.exp(-2*friction_coeff*timestep)))
        self.addPerDofVariable("x1", 0)
        self.addUpdateContextState()
        self.addComputePerDof("v", "v + dt*f/m")
        self.addConstrainVelocities()

        for parameter_name in self.GLOBAL_PARAMETERS:
            for idx in range(n_ghosts):
                self.addComputeGlobal(f"f{parameter_name}_g{idx}",
                                      f"-deriv(energy, {parameter_name}_g{idx})")

        self.addComputePerDof("x", "x + 0.5*dt*v")
        self.addComputePerDof("v", "a*v + b*sqrt(kT/m)*gaussian")
        self.addComputePerDof("x", "x + 0.5*dt*v")
        self.addComputePerDof("x1", "x")
        self.addConstrainPositions()
        self.addComputePerDof("v", "v + (x-x1)/dt")

        for idx in range(n_ghosts):
            for parameter_name in self.GLOBAL_PARAMETERS:
                self.addComputeGlobal(f"{parameter_name}_g{idx}",
                                      f"max(min({parameter_name}_g{idx}"
                                      f"+dt*(f{parameter_name}_g{idx}/{attr_fric_coeffs[parameter_name]}"
                                      f"+sqrt(2*kT/(dt*{attr_fric_coeffs[parameter_name]}))*gaussian),"
                                      f"{attr_bounds[parameter_name][1]}),{attr_bounds[parameter_name][0]})")

        compute_tc_string = ""
        for idx in range(n_ghosts):
            compute_tc_string += f"charge_g{idx}"
            if idx != n_ghosts - 1:
                compute_tc_string += " + "
        self.addComputeGlobal("tot_charge", compute_tc_string)

        for idx in range(n_ghosts):
            self.addComputeGlobal(f"charge_g{idx}",
                                  f"charge_g{idx} - (tot_charge - {const_charge})/{n_ghosts}")
