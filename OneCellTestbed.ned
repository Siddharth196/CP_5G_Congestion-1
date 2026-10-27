import inet.networklayer.configurator.ipv4.Ipv4NetworkConfigurator;
import inet.networklayer.ipv4.RoutingTableRecorder;
import inet.node.ethernet.Eth10G;
import inet.node.inet.Router;
import inet.node.inet.StandardHost;
import simu5g.common.binder.Binder;
import simu5g.common.carrierAggregation.CarrierAggregation;
import simu5g.nodes.NrUe;
import simu5g.nodes.Upf;
import simu5g.nodes.gNodeB;
import simu5g.world.radio.LteChannelControl;

// One cell shaped like the integration team's srsRAN testbed: a single
// gNB, 3-4 static UEs, no neighbour or interference cells (omnetpp.ini,
// OneCell* configs). The gNB keeps the name macroGnb so it gets MacNodeId 1
// and parse_vectors2.py maps it like the urban scenario's macro cell.
network OneCellTestbed
{
    parameters:
        int numUEs = default(4);
        @display("bgb=1000,1000;bgg=100,1,grey95");

    submodules:
        channelControl: LteChannelControl {
            @display("p=50,25;is=s");
        }
        routingRecorder: RoutingTableRecorder {
            @display("p=50,75;is=s");
        }
        configurator: Ipv4NetworkConfigurator {
            @display("p=50,125;is=s");
        }
        binder: Binder {
            @display("p=50,175;is=s");
        }
        carrierAggregation: CarrierAggregation {
            @display("p=50,258;is=s");
        }
        server: StandardHost {
            @display("p=100,500;is=n;i=device/server");
        }
        router: Router {
            @display("p=183,347;i=device/smallrouter");
        }
        upf: Upf {
            @display("p=305,534");
        }
        macroGnb: gNodeB {
            @display("p=500,500;is=vl");
        }
        ue[numUEs]: NrUe {
            @display("p=600,600;is=l");
        }

    connections allowunconnected:
        server.pppg++ <--> Eth10G <--> router.pppg++;
        router.pppg++ <--> Eth10G <--> upf.filterGate;
        upf.pppg++ <--> Eth10G <--> macroGnb.ppp;
}
