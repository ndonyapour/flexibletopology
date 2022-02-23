#Target: 1. Build a system of ghost atoms in the BRD binding pocket.
#        2. Carry out the assembly simulations in that protein environment.
#       {Minimization (water and protein only) is essential to remove bad contacts before simulation}

#Inputs: 1. BRD psf and crd/pdb, 2. mlforce pytorch model file, 3. Target molecule data (pkl file)
#Outputs:

#import ipdb
import sys
import os
import os.path as osp
import pickle as pkl
import numpy as np
import mdtraj as mdj
import pandas as pd

import simtk.openmm.app as omma
import openmm as omm
import simtk.unit as unit
from sys import stdout
import time

from flexibletopology.utils.integrators import CustomLGIntegrator
from flexibletopology.utils.reporters import H5Reporter
from flexibletopology.utils.openmmutils import read_params
import mlforce

import warnings
warnings.filterwarnings("ignore")


#Path to openmm correct version and plug-in as used in the mlforce installation
omm.Platform.loadPluginsFromDirectory(
    '/home/bosesami/anaconda3/pkgs/openmm-7.7.0-py39h9717219_0/lib/plugins')
#    omm.Platform.getDefaultPluginsDirectory())

run = int(sys.argv[1])

# 1. Path to the folder where the target model(.pt) and
# data(pkl) files are stored. (Generated by the create_model_features.py)
# 2. BRD system psf and pdb files are stored.
INPUTS_PATH = './inputs'
SYSTEM_PSF = osp.join(INPUTS_PATH, 'brd2.psf')
SYSTEM_PDB = osp.join(INPUTS_PATH, 'brd_nvt.pdb')
SAVE_PATH = './inputs'

# MD simulations settings
GHOST_MASS = 10
TEMPERATURE = 300.0 * unit.kelvin
FRICTION_COEFFICIENT = 10.0 / unit.picosecond
TIMESTEP = 1 * unit.femtosecond

# Set input and output files name
NUM_STEPS = 10000
REPORT_STEPS = 100
PLATFORM = 'CUDA'

MLFORCESCALE = 1
TARGET_IDX = 124


# MLforce part (TARGET_IDX is the index of the target molecule in the openchem_mols.pkl)
#if PLATFORM == 'Reference' or PLATFORM == 'OpenCL':
#    MODEL_NAME = 'ani_model_cpu.pt'
#else:
#    MODEL_NAME = 'ani_model_cuda.pt'

MODEL_NAME = 'ani_model_cuda.pt'

OUTPUTS_PATH = f'sim_outputs/T{TARGET_IDX}/run{run}'
MODEL_PATH = osp.join(INPUTS_PATH, MODEL_NAME)
DATA_FILE = f'T{TARGET_IDX}_ani.pkl'
PDB = f'traj{TARGET_IDX}.pdb'
SIM_TRAJ = f'traj{TARGET_IDX}.dcd'
H5REPORTER_FILE = f'traj{TARGET_IDX}.h5'
TARGET_PDB = f'target{TARGET_IDX}.pdb'

def getParameters(sim, n_ghosts):
    pars = sim.context.getParameters()
    par_array = np.zeros((n_ghosts,4))
    for i in range(n_ghosts):
        tmp_charge = pars[f'charge_g{i}']
        tmp_sigma = pars[f'sigma_g{i}']
        tmp_epsilon = pars[f'epsilon_g{i}']
        tmp_lambda = pars[f'lambda_g{i}']
        par_array[i] = np.array([tmp_charge,tmp_sigma,tmp_epsilon,tmp_lambda])
    return par_array

def read_target_mol_info(data_file_name):

    dataset_path = osp.join(INPUTS_PATH,
                            data_file_name)

    with open(dataset_path, 'rb') as pklf:
        data = pkl.load(pklf)

    return data['target_coords'], data['target_signals'], data['target_features']

if __name__ == '__main__':

    start_time = time.time()

    # reading the target features from the data file
    target_pos, target_signals, target_features = read_target_mol_info(
        DATA_FILE)
    # print('tf', target_features)
    n_ghosts = target_pos.shape[0]

    pos_file = sys.argv[2]
    par_file = sys.argv[3]

    sys_positions = np.loadtxt(pos_file)
    initial_signals = np.loadtxt(par_file)

    #load the positions
    print("Loading pdb..")
    n_part_system = len(sys_positions) - n_ghosts

    print("Loading psf..")
    # load in psf and add ghost particles
    psf = omma.CharmmPsfFile(SYSTEM_PSF)

    pdb = mdj.load_pdb('minim_'+str(n_ghosts)+'.pdb')

    psf.setBox(pdb.unitcell_lengths[0][0] * unit.nanometers,
               pdb.unitcell_lengths[0][1] * unit.nanometers,
               pdb.unitcell_lengths[0][2] * unit.nanometers)

    # reading FF params
    params = read_params('toppar.str', INPUTS_PATH)

    print("Creating system..")
    system = psf.createSystem(params,
                              nonbondedMethod=omma.forcefield.CutoffPeriodic,
                              nonbondedCutoff=1*unit.nanometers,
                              constraints=omma.forcefield.AllBonds)

    print("Adding ghosts to topology..")
    psf_ghost_chain = psf.topology.addChain(id='G')
    psf_ghost_res = psf.topology.addResidue('ghosts',
                                            psf_ghost_chain)

    # creating a list of charges, sigmas and epsilons 
    # to be used in ga_sys custom-nonbonded force later
    sys_sigma = []
    sys_epsilon = []
    sys_charge = []
    ep_convert = -0.2390057
    for atom in psf.atom_list:
        # in units of elementary charge
        sys_charge.append(atom.charge)
        # now in units of nm
        sys_sigma.append(atom.type.rmin*0.1)
        # now a positive number in kJ/mol
        sys_epsilon.append(atom.type.epsilon/ep_convert)

    # adding ghost particles to the system
    for i in range(n_ghosts):
        system.addParticle(GHOST_MASS)
        psf.topology.addAtom('G{0}'.format(i),
                             omma.Element.getBySymbol('Ar'),
                             psf_ghost_res,
                             'G{0}'.format(i))

    # bounds on the signals  
    bounds = {'charge': (-1.27, 2.194),
              'sigma': (0.022, 0.23),
              'epsilon': (0.037, 2.63),
              'lambda': (0.0, 1.0)}

    ###### FORCES (This will go to util)

    nb_forces = []
    cnb_forces = []
    for i,force in enumerate(system.getForces()):
        force.setForceGroup(i)
        if force.__class__.__name__ == 'NonbondedForce':
            nb_forces.append(force.getForceGroup())
        if force.__class__.__name__ == 'CustomNonbondedForce':
            cnb_forces.append(force.getForceGroup())

    for fidx in nb_forces:
        nb_force = system.getForce(fidx)
        for i in range(n_ghosts):
            nb_force.addParticle(0.0, #charge
                                 0.2, #sigma (nm)
                                 0.0) #epsilon (kJ/mol)
    for fidx in cnb_forces:
        cnb_force = system.getForce(fidx)

        for gh_idx in range(n_ghosts):
            cnb_force.addParticle([0.00001])

        cnb_force.addInteractionGroup(set(range(n_part_system)),
                                      set(range(n_part_system)))
        cnb_force.addInteractionGroup(set(range(n_part_system,n_part_system + n_ghosts)),
                                      set(range(n_part_system,n_part_system + n_ghosts)))

        num_exclusion = cnb_force.getNumExclusions()

    exclusion_list=[]
    for i in range(num_exclusion):
        exclusion_list.append(cnb_force.getExclusionParticles(i))

    # 1. mlforce section
    mlforce_group = 30
    # indices of ghost particles in the topology
    ghost_particle_idxs = [gh_idx for gh_idx in range(n_part_system,(n_part_system+n_ghosts))]

    # Samik: Need to work on this for brd: force weights for "charge", "sigma", "epsilon", "lambda"
    signal_force_weights = [4000.0, 50.0, 100.0,2000.0]

    exmlforce = mlforce.PyTorchForce(file=MODEL_PATH,
                                     targetFeatures=target_features,
                                     particleIndices=ghost_particle_idxs,
                                     signalForceWeights=signal_force_weights,
                                     scale=MLFORCESCALE)

    exmlforce.setForceGroup(mlforce_group)
    system.addForce(exmlforce)


    # 2. custom centroid bond force between each ghost atom and the 82ASN NH2 group
    trj = mdj.load_pdb(SYSTEM_PDB)
    anchor_idxs = []
    for i in range(10,12):
        anchor_idxs.append(trj.top.residue(81).atom(i).index)
    cbf = omm.CustomCentroidBondForce(2, "0.5*k*step(distance(g1,g2) - d0)*(distance(g1,g2) - d0)^2")
    cbf.addGlobalParameter('k', 1000)
    cbf.addGlobalParameter('d0', 0.5)
    anchor_grp_idx = cbf.addGroup(anchor_idxs)
    for gh_idx in range(n_ghosts):
        gh_grp_idx = cbf.addGroup([ghost_particle_idxs[gh_idx]])
        cbf.addBond([anchor_grp_idx, gh_grp_idx])
    system.addForce(cbf)

    # 4. the GS_FORCE (ghost-system non-bonded force)
    gs_force_idxs = []
    print("Adding ghost-system forces to system..")
    for gh_idx in range(n_ghosts):
        energy_function = f'4*lambda_g{gh_idx}*epsilon*(sor12-sor6)+138.9417*lambda_g{gh_idx}*charge1*charge_g{gh_idx}/r;'
        energy_function += 'sor12 = sor6^2; sor6 = (sigma/r)^6;'
        energy_function += f'sigma = 0.5*(sigma1+sigma_g{gh_idx}); epsilon = sqrt(epsilon1*epsilon_g{gh_idx})'
        gs_force = omm.CustomNonbondedForce(energy_function)

        gs_force.addPerParticleParameter('charge')
        gs_force.addPerParticleParameter('sigma')
        gs_force.addPerParticleParameter('epsilon')

        # set to initial values
        gs_force.addGlobalParameter(f'charge_g{gh_idx}', initial_signals[gh_idx, 0])
        gs_force.addGlobalParameter(f'sigma_g{gh_idx}', initial_signals[gh_idx, 1])
        gs_force.addGlobalParameter(f'epsilon_g{gh_idx}', initial_signals[gh_idx, 2])
        gs_force.addGlobalParameter(f'lambda_g{gh_idx}', initial_signals[gh_idx, 3])
        gs_force.addGlobalParameter(f'assignment_g{gh_idx}', 0)

        # adding the del(signal)s [needed in the integrator]
        gs_force.addEnergyParameterDerivative(f'lambda_g{gh_idx}')
        gs_force.addEnergyParameterDerivative(f'charge_g{gh_idx}')
        gs_force.addEnergyParameterDerivative(f'sigma_g{gh_idx}')
        gs_force.addEnergyParameterDerivative(f'epsilon_g{gh_idx}')

        # adding the systems params to the force
        for p_idx in range(n_part_system):
            gs_force.addParticle(
                [sys_charge[p_idx], sys_sigma[p_idx], sys_epsilon[p_idx]])

        # for each force term you need to add ALL the particles even
        # though we only use one of them!
        for p_idx in range(n_ghosts):
            gs_force.addParticle(
                [initial_signals[p_idx, 1], initial_signals[p_idx, 2], initial_signals[p_idx, 3]])

        # interaction between ghost and system    
        gs_force.addInteractionGroup(set(range(n_part_system)),
                                     set([n_part_system + gh_idx]))

        for j in range(len(exclusion_list)):
            gs_force.addExclusion(exclusion_list[j][0], exclusion_list[j][1])

        # periodic cutoff
        gs_force.setNonbondedMethod(gs_force.CutoffPeriodic)
        # cutoff distance in nm
        gs_force.setCutoffDistance(1.0)
        # adding the force to the system
        gs_force_idxs.append(system.addForce(gs_force))

    system.addForce(omm.MonteCarloBarostat(1*unit.bar, 300*unit.kelvin))


    # Set up platform
    if PLATFORM == 'CUDA':
        print("Using CUDA platform..")
        platform = omm.Platform.getPlatformByName('CUDA')
        prop = dict(CudaPrecision='double')

    elif PLATFORM == 'OpenCL':
        print("Using OpenCL platform..")
        platform = omm.Platform.getPlatformByName('OpenCL')
        prop = dict(OpenCLPrecision='double')

    else:
        print("Using Reference platform..")
        prop = {}
        platform = omm.Platform.getPlatformByName('Reference')

    coeffs = {'lambda': 1000000,
              'charge': 5000000,
              'sigma': 10000000,
              'epsilon': 1000000}

    integrator = CustomLGIntegrator(n_ghosts, TEMPERATURE, FRICTION_COEFFICIENT,
                                    TIMESTEP, coeffs=coeffs, bounds=bounds)

    simulation = omma.Simulation(psf.topology, system, integrator,
                                 platform, prop)


    print('Platform',PLATFORM)
    #print('Simulation object', simulation.context)

    simulation.context.setPositions(sys_positions)
    begin = time.time()

    # add reporers
    if not osp.exists(OUTPUTS_PATH):
        os.makedirs(OUTPUTS_PATH)

    # create a pdb file from initial positions
    #omma.PDBFile.writeFile(topology, init_pos,
    #                       open(osp.join(OUTPUTS_PATH, PDB), 'w'))
    #omma.PDBFile.writeFile(topology, target_pos,
    #                       open(osp.join(OUTPUTS_PATH, TARGET_PDB), 'w'))

    simulation.reporters.append(H5Reporter(osp.join(OUTPUTS_PATH, H5REPORTER_FILE),
                                           reportInterval=REPORT_STEPS,
                                           groups=mlforce_group, num_ghosts=n_ghosts))

    simulation.reporters.append(omma.StateDataReporter(stdout, REPORT_STEPS,
                                                       step=True,
                                                       potentialEnergy=True,
                                                       temperature=True))

    simulation.reporters.append(mdj.reporters.DCDReporter(osp.join(OUTPUTS_PATH, SIM_TRAJ),
                                                          REPORT_STEPS))


    simulation.step(NUM_STEPS)

    #apply PBC to the saved trajectory
    pdb = mdj.load_pdb(osp.join(INPUTS_PATH, PDB))
    traj = mdj.load_dcd(osp.join(outputs_path, SIM_TRAJ), top=topology)
    traj = traj.center_coordinates()
    traj.save_dcd(osp.join(outputs_path, SIM_TRAJ))


    print("Simulations Ends")
    print(f"Simulation Steps: {NUM_STEPS}")
    end = time.time()
    print(f"Run time = {np.round(end - begin, 3)}s")
    simulation_time = round((TIMESTEP * NUM_STEPS).value_in_unit(unit.nanoseconds),
                            6)
    print(f"Simulation time: {simulation_time}ns")