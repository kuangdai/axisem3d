// axisem.cpp
// created by Kuangdai on 26-Mar-2016 
// main of AxiSEM3D

#include "axisem.h"
#include "MultilevelTimer.h"

#include "XMPI.h"
#include "eigenc.h"
#include "eigenp.h"

int axisem_main(int argc, char *argv[]) {
    
    try {
        
        // variable sets
        PreloopVariables pl;
        SolverVariables sv;
        
        // initialize mpi
        XMPI::initialize(argc, argv);
        
        //////// spectral-element constants
        SpectralConstants::initialize(nPol);  
        
        //////// input parameters 
        int verbose;
        Parameters::buildInparam(pl.mParameters, verbose);
        
        //////// preloop timer
        MultilevelTimer::initialize(Parameters::sOutputDirectory + "/develop/preloop_timer.txt", 4);
        if (pl.mParameters->getValue<bool>("DEVELOP_DIAGNOSE_PRELOOP")) MultilevelTimer::enable();
        
        //////// exodus model and attenuation parameters 
        MultilevelTimer::begin("Exodus", 0);
        ExodusModel::buildInparam(pl.mExodusModel, *(pl.mParameters), pl.mAttParameters, verbose);
        MultilevelTimer::end("Exodus", 0);
        
        //////// fourier field 
        MultilevelTimer::begin("NrField", 0);
        NrField::buildInparam(pl.mNrField, *(pl.mParameters), pl.mExodusModel->getROuter(), verbose);
        MultilevelTimer::end("NrField", 0);
        
        //////// source
        MultilevelTimer::begin("Source", 0);
        Source::buildInparam(pl.mSource, *(pl.mParameters), verbose);
        double srcLat = pl.mSource->getLatitude();
        double srcLon = pl.mSource->getLongitude();
        double srcDep = pl.mSource->getDepth();
        MultilevelTimer::end("Source", 0);
        
        //////// 3D models 
        MultilevelTimer::begin("3D Models", 0);
        Volumetric3D::buildInparam(pl.mVolumetric3D, *(pl.mParameters), pl.mExodusModel, 
            srcLat, srcLon, srcDep, verbose);
        Geometric3D::buildInparam(pl.mGeometric3D, *(pl.mParameters), verbose);
        OceanLoad3D::buildInparam(pl.mOceanLoad3D, *(pl.mParameters), verbose);
        MultilevelTimer::end("3D Models", 0);
        
        //////// mesh, phase 1
        // define mesh
        MultilevelTimer::begin("Mesh Definition", 0);
        pl.mMesh = new Mesh(pl.mExodusModel, pl.mNrField, srcLat, srcLon, srcDep, *(pl.mParameters));
        pl.mMesh->setVolumetric3D(pl.mVolumetric3D);
        pl.mMesh->setGeometric3D(pl.mGeometric3D);
        pl.mMesh->setOceanLoad3D(pl.mOceanLoad3D);
        MultilevelTimer::end("Mesh Definition", 0);
        
        // build unweighted local mesh 
        MultilevelTimer::begin("Unweighted Mesh", 0);
        pl.mMesh->buildUnweighted();
        MultilevelTimer::end("Unweighted Mesh", 0);
        
        //////// static variables in solver, mainly FFTW
        MultilevelTimer::begin("Initialize FFTW", 0);
        initializeSolverStatic(pl.mMesh->getMaxNr()); 
        MultilevelTimer::end("Initialize FFTW", 0);
        
        //////// dt
        MultilevelTimer::begin("DT", 0);
        double dt = pl.mParameters->getValue<double>("TIME_DELTA_T");
        if (dt < tinyDouble) dt = pl.mMesh->getDeltaT();
        double dt_fact = pl.mParameters->getValue<double>("TIME_DELTA_T_FACTOR");
        if (dt_fact < tinyDouble) dt_fact = 1.0;
        dt *= dt_fact;
        MultilevelTimer::end("DT", 0);
        
        //////// attenuation
        MultilevelTimer::begin("Attenuation", 0);
        AttBuilder::buildInparam(pl.mAttBuilder, *(pl.mParameters), *(pl.mAttParameters), dt, verbose);
        MultilevelTimer::end("Attenuation", 0);
        
        //////// mesh, phase 2
        MultilevelTimer::begin("Weighted Mesh", 0);
        pl.mMesh->setAttBuilder(pl.mAttBuilder);
        pl.mMesh->buildWeighted();
        MultilevelTimer::end("Weighted Mesh", 0);
        
        //////// mesh test 
        // test positive-definiteness and self-adjointness of stiffness and mass matrices
        // better to turn with USE_DOUBLE 
        // pl.mMesh->test();
        // XMPI::barrier();
        // exit(0);
        
        //////// source time function 
        MultilevelTimer::begin("Source Time Function", 0);
        STF::buildInparam(pl.mSTF, *(pl.mParameters), dt, verbose);
        MultilevelTimer::end("Source Time Function", 0);
        
        //////// receivers
        MultilevelTimer::begin("Receivers", 0);
        ReceiverCollection::buildInparam(pl.mReceivers, 
            *(pl.mParameters), srcLat, srcLon, srcDep, verbose);
        MultilevelTimer::end("Receivers", 0);    
        
        //////// computational domain
        MultilevelTimer::begin("Computationalion Domain", 0);
        sv.mDomain = new Domain();
        
        // release mesh
        MultilevelTimer::begin("Release Mesh", 1);
        pl.mMesh->release(*(sv.mDomain));
        MultilevelTimer::end("Release Mesh", 1);
        
        // release source 
        MultilevelTimer::begin("Release Source", 1);
        pl.mSource->release(*(sv.mDomain), *(pl.mMesh));
        MultilevelTimer::end("Release Source", 1);
        
        // release stf 
        MultilevelTimer::begin("Release STF", 1);
        pl.mSTF->release(*(sv.mDomain));
        MultilevelTimer::end("Release STF", 1);
        
        // release receivers
        MultilevelTimer::begin("Release Receivers", 1);
        pl.mReceivers->release(*(sv.mDomain), *(pl.mMesh));
        MultilevelTimer::end("Release Receivers", 1);
        
        // verbose domain 
        MultilevelTimer::begin("Verbose", 1);
        if (verbose) XMPI::cout << sv.mDomain->verbose();
        MultilevelTimer::end("Verbose", 1);
        MultilevelTimer::end("Computationalion Domain", 0);
        
        MultilevelTimer::finalize();
        
        //////////////////////// PREPROCESS DONE ////////////////////////
        
        //////// Newmark
        int infoInt = pl.mParameters->getValue<int>("OPTION_LOOP_INFO_INTERVAL");
        int stabInt = pl.mParameters->getValue<int>("OPTION_STABILITY_INTERVAL");
        sv.mNewmark = new Newmark(sv.mDomain, infoInt, stabInt);
        
        //////// final preparations
        // finalize preloop variables before time loop starts
        pl.finalize();
        // forbid matrix allocation in time loop
        #ifndef NDEBUG
            Eigen::internal::set_is_malloc_allowed(false);
        #endif
            
        //////// GoGoGo
        XMPI::barrier();
        sv.mNewmark->solve();
        
        //////// finalize solver
        // solver 
        sv.finalize();
        // static variables in solver
        finalizeSolverStatic();
        
        // finalize mpi 
        XMPI::finalize();
        
    } catch (const std::exception &e) {
        // print exception
        XMPI::cout.setp(XMPI::rank());
        XMPI::printException(e);
        
        // abort program
        // TODO 
        // MPI_Abort is necessary here. Otherwise, if an exception
        // is thrown from one of the procs, deadlock will occur.
        // But the problem is, how we free memories on other procs?!
        XMPI::abort();
    }
    
    return 0;
}

#include "SolverFFTW.h"
#include "SolverFFTW_1.h"
#include "SolverFFTW_3.h"
#include "SolverFFTW_N3.h"
#include "SolverFFTW_N6.h"
#include "SolverFFTW_N9.h"
#include "PreloopFFTW.h"
#include "SolidElement.h"
#include "FluidElement.h"

extern void initializeSolverStatic(int maxNr) {
    // fftw
    SolverFFTW::importWisdom();
    SolverFFTW_1::initialize(maxNr);
    SolverFFTW_3::initialize(maxNr); 
    SolverFFTW_N3::initialize(maxNr);
    SolverFFTW_N6::initialize(maxNr);
    SolverFFTW_N9::initialize(maxNr);
    SolverFFTW::exportWisdom();
    // PreloopFFTW::initialize(maxNr);
    // element
    SolidElement::initWorkspace(maxNr / 2);
    FluidElement::initWorkspace(maxNr / 2);
};

extern void finalizeSolverStatic() {
    // fftw
    SolverFFTW_1::finalize();
    SolverFFTW_3::finalize(); 
    SolverFFTW_N3::finalize();
    SolverFFTW_N6::finalize();
    SolverFFTW_N9::finalize();
    PreloopFFTW::finalize();
};
